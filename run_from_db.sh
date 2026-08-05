#!/bin/bash
# Works when `python` is Python 2 — launcher finds python3 automatically.
cd "$(dirname "$0")"
exec python tmone_report.py --from-db --active-only "$@"
