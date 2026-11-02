#!/bin/bash
# Always use Python 3 on Linux
cd "$(dirname "$0")"
exec python3 tmone_report.py "$@"