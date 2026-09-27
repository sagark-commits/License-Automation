#!/bin/bash
# Launch the TMONE license dashboard (Streamlit) on the internal network.
# Usage: ./run_dashboard.sh [port]
cd "$(dirname "$0")"
PORT="${1:-8501}"
PY="${PYTHON3:-python3.9}"
exec "$PY" -m streamlit run dashboard.py --server.port "$PORT" --server.address 0.0.0.0
