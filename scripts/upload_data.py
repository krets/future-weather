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
    LOG.info(f"Running query with step={step} seconds and hours={hours}")
    now = int(time.time())
    yesterday = now - 24 * 60 * 60

    LOG.info("Pressure query")
    queries = [('avg(deriv(bmp280_pressure[4h]))*4*60*60', yesterday, now)]
    queries.extend(build_prediction_queries('avg(deriv(yrno_air_pressure_at_sea_level{hours="%s"}[4h] offset %sh))*4*60*60', hours))
    pressure_data = run_queries(base_url, queries, step)

    LOG.info("Wind query")
    wind_queries = [('openweather_windspeed', yesterday, now)]
    wind_queries.extend(build_prediction_queries('yrno_wind_speed{hours="%s"} offset %sh', hours))
    wind_data = run_queries(base_url, wind_queries, step)

    LOG.info("Uploading json data files")
    for name, data in [('wind', wind_data), ('pressure', pressure_data)]:
        data_file = "%s_data.json" % name
        with open(data_file, 'w') as fh:
            json.dump(data, fh)
        LOG.debug("Dumped: %s", data_file)
        cmd = ["/usr/bin/scp", "-q", data_file, target+"/%s" % data_file]
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


def build_prediction_queries(template, hours):
    LOG.debug("Creating prediction queries for %d hours", hours)
    now = int(time.time())
    queries = []
    for i in range(hours):
        hour = i + 1
        queries.append((
            template % (hour, hour),
            now + i * 60 * 60,
            now + hour * 60 * 60,
        ))
    return queries

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
