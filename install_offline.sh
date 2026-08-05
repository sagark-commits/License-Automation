#!/bin/bash
# Run ON THE AIR-GAPPED SERVER (no internet required).
# Expects: ./wheels/*.whl and ./<app-folder>/

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
WHEELS="$ROOT/wheels"
PY="${PYTHON3:-python3.9}"

# Detect app folder
if [[ -d "$ROOT/license-utilization-automation" ]]; then
  APP="$ROOT/license-utilization-automation"
elif [[ -d "$ROOT/cc-license-reporter" ]]; then
  APP="$ROOT/cc-license-reporter"
else
  APP="$(find "$ROOT" -maxdepth 1 -type d ! -name wheels ! -name "$(basename "$ROOT")" | head -1)"
fi

echo "=== Offline package install (air-gapped) ==="
echo "Python: $($PY --version 2>&1 || true)"
echo "App:    $APP"
echo "Wheels: $WHEELS"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "ERROR: $PY not found. Install Python 3.9+ from your internal OS packages." >&2
  echo "  Example: yum install python39 python39-pip" >&2
  exit 1
fi

if [[ ! -d "$WHEELS" ]]; then
  echo "ERROR: missing $WHEELS" >&2
  exit 1
fi

WHEEL_COUNT=$(ls -1 "$WHEELS"/*.whl 2>/dev/null | wc -l)
echo "Wheel files found: $WHEEL_COUNT"
if [[ "$WHEEL_COUNT" -eq 0 ]]; then
  echo "ERROR: no .whl files. Rebuild USB bundle on a PC with internet/Docker." >&2
  exit 1
fi

PYTAG="cp$($PY -c 'import sys; print(f"{sys.version_info.major}{sys.version_info.minor}")')"
echo "Server Python tag: $PYTAG"
if ! ls "$WHEELS"/*${PYTAG}*.whl >/dev/null 2>&1; then
  echo "ERROR: no wheels for $PYTAG. Rebuild bundle with matching -PythonVersion." >&2
  ls "$WHEELS"/*.whl | sed -n 's/.*-\(cp[0-9]*\)-.*/\1/p' | sort -u | head -10 >&2
  exit 1
fi

echo "Installing from local wheels (NO internet)..."
$PY -m pip install --no-index --find-links="$WHEELS" "$WHEELS"/*.whl

echo ""
echo "Verify imports:"
$PY - << 'PY'
import pandas, openpyxl, yaml, psycopg2
print("OK: pandas", pandas.__version__)
print("OK: openpyxl", openpyxl.__version__)
print("OK: PyYAML / yaml")
print("OK: psycopg2", psycopg2.__version__)
PY

echo ""
echo "Installed successfully."
echo "Next:"
echo "  cd $APP"
echo "  cp db_config.yaml.example db_config.yaml"
echo "  # edit credentials"
echo "  $PY run_monthly_from_db.py -m YYYY-MM"