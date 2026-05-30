#!/data/data/com.termux/files/usr/bin/env bash
# Start Hermes Tasks Dashboard
PORT=${1:-8081}
cd "$(dirname "$0")"
echo "Starting Hermes Tasks Dashboard on http://localhost:$PORT"
python3 app.py $PORT
