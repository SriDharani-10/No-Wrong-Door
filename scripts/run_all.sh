#!/usr/bin/env bash
# Starts the two mock source services and the unified API. Ctrl-C stops all three.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${BENEFITS_FAILURE_RATE:=0.15}"
export BENEFITS_FAILURE_RATE

python3 services/rest_service.py --port 8081 &
REST=$!
python3 services/xml_service.py  --port 8082 &
XML=$!

# give the sources a moment to bind before the API starts probing them
sleep 0.5

python3 app/main.py --port 8080 &
API=$!

trap 'kill $REST $XML $API 2>/dev/null || true' EXIT INT TERM

echo
echo "  Resident Index (REST)     http://127.0.0.1:8081/residents?page=1"
echo "  Benefits Register (XML)   http://127.0.0.1:8082/records"
echo "  No Wrong Door API         http://127.0.0.1:8080/health"
echo

wait
