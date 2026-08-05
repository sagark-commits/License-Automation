#!/bin/bash
# Run ON THE AIR-GAPPED SERVER (no internet).
# Expects: ./wheels/*.whl and ./license-utilization-automation/

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$ROOT/license-utilization-automation"
WHEELS="$ROOT/wheels"
PY="${PYTHON3:-python3.9}"
if ! command -v "$PY" >/dev/null 2>&1; then
  PY="${PYTHON3:-python3}"
fi

echo "=== TMONE offline install ==="
echo "Python: $($PY --version 2>&1)"
echo "Pip:    $($PY -m pip --version 2>&1)"
echo "Wheels: $WHEELS"

if [[ ! -d "$WHEELS" ]]; then
  echo "ERROR: missing $WHEELS" >&2
  exit 1
fi

WHEEL_COUNT=$(ls -1 "$WHEELS"/*.whl 2>/dev/null | wc -l)
echo "Wheel files found: $WHEEL_COUNT"
if [[ "$WHEEL_COUNT" -eq 0 ]]; then
  echo "ERROR: no .whl files in $WHEELS" >&2
  echo "The USB bundle is incomplete. Rebuild on Windows with:" >&2
  echo "  .\\prepare_usb_bundle.ps1 -PythonVersion 3.9" >&2
  echo "Then copy the NEW zip (must contain 10+ wheel files)." >&2
  exit 1
fi

PYTAG="cp$($PY -c 'import sys; print(f"{sys.version_info.major}{sys.version_info.minor}")')"
echo "Server Python tag: $PYTAG"
if ! ls "$WHEELS"/*${PYTAG}*.whl >/dev/null 2>&1; then
  echo "ERROR: no wheels for $PYTAG in bundle." >&2
  echo "On server run: python3 --version" >&2
  echo "Rebuild bundle on Windows with matching -PythonVersion." >&2
  echo "Bundle has tags:" >&2
  ls "$WHEELS"/*.whl | sed -n 's/.*-\(cp[0-9]*\)-.*/\1/p' | sort -u | head -5
  exit 1
fi

echo "Installing from local wheels (no internet)..."
$PY -m pip install --no-index --find-links="$WHEELS" "$WHEELS"/*.whl

echo ""
echo "Verify imports:"
$PY - << 'PY'
import pandas, openpyxl, yaml, psycopg2
print("OK: pandas", pandas.__version__)
print("OK: openpyxl", openpyxl.__version__)
print("OK: psycopg2", psycopg2.__version__)
PY

echo ""
echo "Installed. Next:"
echo "  cd $APP"
echo "  cp db_config.yaml.example db_config.yaml"
echo "  python3.9 run_full_login.py -m 2026-06 --from-db"
