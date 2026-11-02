#!/bin/bash
# Run on ANY machine WITH internet that matches the target server:
#   - Same CPU arch (usually x86_64)
#   - Same Python minor version as server (python3 --version)
#
# Usage:
#   chmod +x download_offline_packages.sh
#   ./download_offline_packages.sh
#
# Copy the whole offline_bundle/ folder to the server via USB/scp jump host.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BUNDLE="$ROOT/offline_bundle"
WHEELS="$BUNDLE/wheels"
PY="${PYTHON3:-python3}"

echo "Using: $($PY --version)"
mkdir -p "$WHEELS"

$PY -m pip install --upgrade pip wheel setuptools
$PY -m pip download -r "$ROOT/requirements.txt" -d "$WHEELS"

# Copy application files into bundle
APP="$BUNDLE/license-utilization-automation"
mkdir -p "$APP"
for f in tmone_report.py tmone_report_main.py db_connections.py tmone_db_mode.py \
         db_usage_queries.py excel_format.py tenants.yaml requirements.txt \
         db_config.yaml.example run_from_db.sh; do
  if [[ -f "$ROOT/$f" ]]; then
    cp "$ROOT/$f" "$APP/"
  fi
done
cp "$ROOT/install_offline.sh" "$BUNDLE/"

cat > "$BUNDLE/README_OFFLINE.txt" << 'EOF'
OFFLINE INSTALL (on server with no internet)
==========================================
1. Copy this entire offline_bundle folder to the server.
2. cd offline_bundle
3. chmod +x install_offline.sh
4. ./install_offline.sh
5. cd license-utilization-automation
6. cp db_config.yaml.example db_config.yaml  # edit user/password
7. python3 tmone_report.py --from-db --arc ARC-1 --active-only --month 2026-06
EOF

echo ""
echo "Done. Transfer offline_bundle/ to the server."
echo "Wheels: $(ls -1 "$WHEELS" | wc -l) files"