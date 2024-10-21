#!/usr/bin/env python3
import time
import requests
import json
import subprocess
import argparse
import logging

# Set up logger
LOG = logging.getLogger(__name__)
_BASE_URL = "http://%s/api/v1/query_range"

def query(step, hours, base_url, target):
    LOG.info(f"Running query with step={step} seconds and hours={hours}; output to '{target}'")
    now = int(time.time())
    yesterday = now - 24 * 60 * 60

    data = {}
    LOG.info("Pressure query")
    window = "4h"
    multiplier = "4.3*60*60"
    queries = [(f'avg(deriv(bmp280_pressure[{window}]))*{multiplier}', yesterday, now)]
    queries.extend(build_prediction_queries(f'avg(deriv(yrno_air_pressure_at_sea_level{{hours="%s"}}[{window}] offset %sh))*{multiplier}', hours))
    raw_merged_data = run_queries(base_url, queries, step)
    smoothed_data = smooth(raw_merged_data, window_size=5)
    data['pressure'] = smoothed_data

    LOG.info("Wind query")
    wind_queries = [('openweather_windspeed', yesterday, now)]
    wind_queries.extend(build_prediction_queries('yrno_wind_speed{hours="%s"} offset %sh', hours))
    data['wind'] = run_queries(base_url, wind_queries, step)

    LOG.info("Temperature query")
    temp_queries = [('yrno_air_temperature{hours="0"}', yesterday, now)]
    temp_queries.extend(build_prediction_queries('yrno_air_temperature{hours="%s"} offset %sh', hours))
    data['temperature'] = run_queries(base_url, temp_queries, step)


    LOG.info("Precipitation query")
    prec_queries = [('yrno_precipitation_amount{hours="0"}', yesterday, now)]
    prec_queries.extend(build_prediction_queries('yrno_precipitation_amount{hours="%s"} offset %sh', hours))
    data['precipitation'] = run_queries(base_url, prec_queries, step)

    LOG.info("Uploading json data files")
    for name, values in data.items():
        data_file = "%s_data.json" % name
        with open(data_file, 'w') as fh:
            json.dump(values, fh)
        LOG.debug("Dumped: %s", data_file)
        cmd = ["scp", "-q", data_file, target+"/%s" % data_file]
        subprocess.run(cmd)


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
        data.extend(resp['data']['result'][0]['values'])
    return data


def build_prediction_queries(template, hours, index_offset=1):
    LOG.debug("Creating prediction queries for %d hours", hours)
    now = int(time.time())
    queries = []
    for i in range(hours):
        hour = i + index_offset
        queries.append((
            template % (hour, hour),
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
    query(args.step, args.hours, _BASE_URL % args.source, args.target)


if __name__ == '__main__':
    LOG.addHandler(logging.StreamHandler())
    LOG.handlers[-1].setFormatter(logging.Formatter(logging.BASIC_FORMAT))
    main()
