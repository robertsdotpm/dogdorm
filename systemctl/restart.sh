#!/bin/bash
set -e  # exit immediately if a command fails

# Only defined if debug enabled.
curl localhost:8000/sql_export

# Stop the service
./stop.sh

# Start the service
./start.sh
