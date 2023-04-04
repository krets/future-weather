# Weather Data Prediction Script
These are some things I put together to track weather changes because my dog get's bothered. This is not intended for
reuse, but I have done some minimal clean-up for public sharing.

## Requirements
 - Prometheus server
 - yrnocollector
 - other pressure collector for actuals (I use a BMP280 with an ESP32 running an exporter.)

## Script
`scripte/upload_data.py`
his is a Python script that retrieves weather data from a Prometheus server and generates predictions for a certain number of hours in the future. The data is obtained through HTTP requests and then uploaded to a remote server via SCP.
This should be run periodically to keep the data fresh.

```
usage: weather.py [-h] [--step STEP] [--hours HOURS] [-v] [-d]

optional arguments:
  -h, --help          show this help message and exit
  --step STEP         Data resolution in seconds (default: 60)
  --hours HOURS       Hours of predicted data (default: 12)
  --source HOST,      Prometheus host and port (default: localhost:9090)
  --target SSH_PATH   SSH target for json upload (example: user@remote:path/to/server)
  -v, --verbose       Enable verbose messages
  -d, --debug         Enable debug messages
```

## HTML
The html file will consume the uploaded json data to display a nice graph using D3.js. The favicon will also be updated 
when there is a pressure change threshold crossed now or in the very near future.