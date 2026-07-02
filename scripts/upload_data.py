#!/usr/bin/env python3
import time
import re
import requests
import json
import subprocess
import argparse
import logging

# Set up logger
LOG = logging.getLogger(__name__)
_QUERY_RANGE_URL = "http://%s/api/v1/query_range"
_LABEL_VALUES_URL = "http://%s/api/v1/label/location/values"


def get_locations(base_url):
    """Discover locations from Prometheus itself (the location label on
    yrno_* series) so this script never needs its own hardcoded list."""
    # Scoped to yrno_* series only -- "location" is a generic label name
    # other exporters (e.g. openweather) may also use with unrelated values.
    resp = requests.get(_LABEL_VALUES_URL % base_url, params={"match[]": '{__name__=~"yrno_.*"}'}).json()
    locations = [loc for loc in resp['data'] if loc]
    if not locations:
        raise RuntimeError("No locations found via label/location/values; is yrno-collector running?")
    return locations


def display_name(location):
    """'NewYorkCity' -> 'New York City'"""
    return re.sub(r'(?<!^)(?=[A-Z])', ' ', location)


def query_location(step, hours, base_url, location):
    LOG.info(f"Querying data for {location}")
    now = int(time.time())
    yesterday = now - 24 * 60 * 60
    loc_filter = f'location="{location}"'

    data = {}
    LOG.info("Pressure query")
    window = "4h"
    multiplier = "4*60*60"
    actual_query = f'avg(deriv(yrno_air_pressure_at_sea_level{{hours="0", {loc_filter}}}[{window}]))*{multiplier}'
    raw_actual_data = run_queries(base_url, [(actual_query, yesterday, now)], step)

    pressure_prediction_template = f'avg(deriv(yrno_air_pressure_at_sea_level{{hours="%s", {loc_filter}}}[{window}] offset %sh))*{multiplier}'
    prediction_queries = build_prediction_queries(pressure_prediction_template, hours)
    raw_prediction_data = run_queries(base_url, prediction_queries, step)
    smoothed_data = smooth(raw_actual_data + raw_prediction_data, window_size=5)
    data['pressure'] = smoothed_data

    LOG.info("Wind query")
    wind_queries = [(f'yrno_wind_speed{{hours="0", {loc_filter}}}', yesterday, now)]
    wind_queries.extend(build_prediction_queries(f'yrno_wind_speed{{hours="%s", {loc_filter}}} offset %sh', hours))
    raw_wind_data = run_queries(base_url, wind_queries, step)
    smoothed_wind_data = smooth(raw_wind_data, window_size=5)
    data['wind'] = smoothed_wind_data

    LOG.info("Temperature query")
    temp_queries = [(f'yrno_air_temperature{{hours="0", {loc_filter}}}', yesterday, now)]
    temp_queries.extend(build_prediction_queries(f'yrno_air_temperature{{hours="%s", {loc_filter}}} offset %sh', hours))
    raw_temp_data = run_queries(base_url, temp_queries, step)
    smoothed_temp_data = smooth(raw_temp_data, window_size=5)
    data['temperature'] = smoothed_temp_data

    LOG.info("Precipitation query")
    prec_queries = [(f'yrno_precipitation_amount{{hours="0", {loc_filter}}}', yesterday, now)]
    prec_queries.extend(build_prediction_queries(f'yrno_precipitation_amount{{hours="%s", {loc_filter}}} offset %sh', hours))
    data['precipitation'] = run_queries(base_url, prec_queries, step)

    return data


def run_queries(base_url, queries, step=60):
    data = []
    for query, start, end in queries:
        params = {
            "query": query,
            "step": step,
            "start": start,
            "end": end,
        }
        resp = requests.get(base_url, params=params).json()
        results = resp['data']['result']
        if not results:
            raise ValueError(f"No results for query '{query}'")
        # A query can match multiple disjoint series in edge cases; merge
        # them into one chronological series rather than assuming exactly one.
        values = [value for series in results for value in series['values']]
        values.sort(key=lambda v: v[0])
        data.extend(values)
    return data


def build_prediction_queries(template, hours, index_offset=1):
    LOG.debug("Creating prediction queries for %d hours", hours)
    now = int(time.time())
    queries = []
    for i in range(hours):
        hour = i + index_offset
        queries.append((
            template.replace('%s', str(hour)),
            now + i * 60 * 60,
            now + hour * 60 * 60,
        ))
    return queries


def smooth(data, window_size=5):
    """
    Smooths the pressure data using a rolling average.

    Parameters:
    - data (list): A list of pressure data in the format [[timestamp, pressure], ...].
    - window_size (int): The size of the rolling window for smoothing.

    Returns:
    - list: A list of smoothed pressure data in the same format.
    """
    smoothed_data = []
    length = len(data)

    # Convert pressure values to float for calculations
    pressures = [float(item[1]) for item in data]

    for i in range(length):
        # Calculate the start and end indices for the rolling window
        start_index = max(0, i - window_size // 2)
        end_index = min(length, i + window_size // 2 + 1)

        # Get the values in the window
        window_values = pressures[start_index:end_index]

        # Calculate the average
        average = sum(window_values) / len(window_values)

        # Append the smoothed value to the result
        smoothed_data.append([data[i][0], average])

    return smoothed_data


def upload_file(local_path, target, remote_name):
    """Upload atomically: scp to a .tmp name, then rename into place over
    SSH, so the site never fetches a half-written file mid-transfer."""
    remote_host, remote_dir = target.split(":", 1)
    tmp_name = remote_name + ".tmp"
    subprocess.run(["scp", "-q", local_path, f"{target}/{tmp_name}"], check=True)
    subprocess.run(["ssh", remote_host, "mv", f"{remote_dir}/{tmp_name}", f"{remote_dir}/{remote_name}"], check=True)


def query(step, hours, source, target):
    LOG.info(f"Running query with step={step} seconds and hours={hours}; output to '{target}'")
    locations = get_locations(source)
    LOG.info(f"Discovered locations: {locations}")
    base_url = _QUERY_RANGE_URL % source

    data_target = target + "/data"
    remote_host, remote_dir = target.split(":", 1)
    subprocess.run(["ssh", remote_host, "mkdir", "-p", remote_dir + "/data"], check=True)

    manifest = [{"key": loc, "label": display_name(loc)} for loc in sorted(locations)]
    with open("locations.json", 'w') as fh:
        json.dump(manifest, fh)
    upload_file("locations.json", data_target, "locations.json")

    for location in locations:
        try:
            data = query_location(step, hours, base_url, location)
        except Exception as e:
            LOG.warning(f"Failed to gather data for {location} ({e}); leaving its published data as-is.")
            continue

        data_file = f"{location}.json"
        with open(data_file, 'w') as fh:
            json.dump(data, fh)
        LOG.debug("Dumped: %s", data_file)
        try:
            upload_file(data_file, data_target, data_file)
        except subprocess.CalledProcessError as e:
            LOG.warning(f"Failed to upload data for {location} ({e}); leaving its published data as-is.")


def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--step', type=int, default=60, help='Data resolution in seconds')
    parser.add_argument('--hours', type=int, default=12, help='Hours of predicted data')
    parser.add_argument('--source', type=str, default='localhost:9090', help='Prometheus host and port')
    parser.add_argument('--target', type=str, default='user@remote:/path/to/data', help='SSH target for json upload')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose messages')
    parser.add_argument('-d', '--debug', action='store_true', help='Enable debug messages')
    args = parser.parse_args()

    # Set up logging
    level = logging.WARNING
    if args.verbose:
        level = logging.INFO
    elif args.debug:
        level = logging.DEBUG


    LOG.setLevel(level)

    # Call query function with arguments
    query(args.step, args.hours, args.source, args.target)


if __name__ == '__main__':
    LOG.addHandler(logging.StreamHandler())
    LOG.handlers[-1].setFormatter(logging.Formatter(logging.BASIC_FORMAT))
    main()
