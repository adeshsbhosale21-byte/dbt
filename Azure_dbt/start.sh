#!/bin/bash
# Start Nginx in the background
nginx -g "daemon off;" &
# Start FastAPI backend in the foreground
python -m app.main
