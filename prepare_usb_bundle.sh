#!/bin/bash
# Run from MobaXterm / Git Bash / WSL
# Usage: ./prepare_usb_bundle.sh [python_version]

set -euo pipefail
PYVER="${1:-3.9}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
BUNDLE="$ROOT/offline_bundle"
WHEELS="$BUNDLE/wheels"
APP="$BUNDLE/license-utilization-automation"
ZIP="$ROOT/tmone_offline_bundle_py${PYVER}.zip"
IMAGE="python:${PYVER}-slim-bookworm"

# MobaXterm uses /drives/c/... — Docker Desktop needs C:/Users/...
to_docker_path() {
  local p="$1"
  if [[ "$p" =~ ^/drives/([a-zA-Z])/(.*)$ ]]; then
    local letter="${BASH_REMATCH[1]}"
    local rest="${BASH_REMATCH[2]}"
    printf '%s:/%s' "${letter^^}" "$rest"
    return
  fi
  if [[ "$p" =~ ^/([a-zA-Z])/(.*)$ ]]; then
    printf '%s:/%s' "${BASH_REMATCH[1]^^}" "${BASH_REMATCH[2]}"
    return
  fi
  if [[ "$p" =~ ^/mnt/([a-zA-Z])/(.*)$ ]]; then
    printf '%s:/%s' "${BASH_REMATCH[1]^^}" "${BASH_REMATCH[2]}"
    return
  fi
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -m "$p"
    return
  fi
  echo "$p"
}

echo "=== TMONE offline USB bundle (Linux wheels via Docker) ==="
echo "Target Python: $PYVER"
echo "Project dir:   $ROOT"

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker is not running. Start Docker Desktop on Windows, then retry." >&2
  exit 1
fi

if [[ ! -f "$ROOT/requirements.txt" ]]; then
  echo "ERROR: requirements.txt not found in $ROOT" >&2
  exit 1
fi

REQ_HOST="$(to_docker_path "$ROOT/requirements.txt")"
WHEELS_HOST="$(to_docker_path "$WHEELS")"
echo "Docker host path (req): $REQ_HOST"

if [[ -d "$BUNDLE" ]]; then
  BAK="$ROOT/offline_bundle_old_$(date +%Y%m%d_%H%M%S)"
  echo "Moving previous bundle to $(basename "$BAK") ..."
  mv "$BUNDLE" "$BAK" 2>/dev/null || rm -rf "$BUNDLE" 2>/dev/null || {
    echo "ERROR: cannot clear offline_bundle — close File Explorer / IDE using that folder" >&2
    exit 1
  }
fi
mkdir -p "$WHEELS" "$APP"

echo "Pulling $IMAGE ..."
docker pull "$IMAGE"

echo "Downloading wheels ..."
CID=$(docker create "$IMAGE" sleep 600)
docker cp "$REQ_HOST" "$CID:/tmp/requirements.txt"
docker start "$CID" >/dev/null
docker exec "$CID" bash -lc "pip install -q --upgrade pip wheel setuptools && mkdir -p /tmp/wheels && pip download -r /tmp/requirements.txt -d /tmp/wheels --platform manylinux2014_x86_64 --python-version ${PYVER} --implementation cp --only-binary=:all:"
docker cp "$CID:/tmp/wheels/." "$WHEELS_HOST/"
docker rm -f "$CID" >/dev/null

for f in tmone_report.py tmone_report_main.py db_connections.py tmone_db_mode.py \
         db_usage_queries.py excel_format.py tenants.yaml requirements.txt \
         db_config.yaml.example run_from_db.sh install_offline.sh \
         dashboard.py run_dashboard.sh run_dashboard.bat; do
  [[ -f "$ROOT/$f" ]] && cp "$ROOT/$f" "$APP/"
done
cp "$ROOT/install_offline.sh" "$BUNDLE/"
cp "$ROOT/SERVER_HANDOFF_README.txt" "$BUNDLE/" 2>/dev/null || true

rm -f "$ZIP"
if command -v zip >/dev/null 2>&1; then
  (cd "$ROOT" && zip -rq "$(basename "$ZIP")" offline_bundle)
fi

COUNT=$(ls -1 "$WHEELS"/*.whl 2>/dev/null | wc -l)
echo ""
echo "SUCCESS"
echo "  Folder: $BUNDLE"
[[ -f "$ZIP" ]] && echo "  Zip:    $ZIP"
echo "  Wheels: $COUNT"
if [[ "$COUNT" -eq 0 ]]; then
  echo "ERROR: no wheels downloaded" >&2
  exit 1
fi
echo ""
echo "Copy tmone_offline_bundle_py${PYVER}.zip (or offline_bundle/) to USB."