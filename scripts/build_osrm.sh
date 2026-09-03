#!/usr/bin/env bash
# Build the OSRM routing graph for the Klang Valley.
#
#   ./scripts/build_osrm.sh
#
# Downloads the Malaysia/Singapore/Brunei extract, crops it to a Klang Valley
# bounding box, then runs OSRM's three-stage MLD pipeline.
#
# Cropping first is the whole trick. The full extract is ~240MB and takes
# osrm-extract several GB of RAM and a good while to process. Every job in
# this project is inside the Klang Valley, so the crop cuts the input by
# roughly 90% and the build finishes in minutes on well under 2GB.
#
# Safe to re-run. Each stage is skipped if its output already exists; pass
# --force to rebuild from what is already downloaded, or --redownload to
# start from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

DATA_DIR="osrm-data"
PBF_URL="https://download.geofabrik.de/asia/malaysia-singapore-brunei-latest.osm.pbf"
SOURCE_PBF="$DATA_DIR/malaysia.osm.pbf"
CROPPED_PBF="$DATA_DIR/klang-valley.osm.pbf"
GRAPH="$DATA_DIR/klang-valley.osrm"

FORCE=0
REDOWNLOAD=0
for arg in "$@"; do
  case "$arg" in
    --force)      FORCE=1 ;;
    --redownload) REDOWNLOAD=1; FORCE=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
note() { printf '    %s\n' "$1"; }

# --- Preflight --------------------------------------------------------------

step "Preflight"

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker Desktop and retry." >&2
  exit 1
fi

DOCKER_MEM_BYTES=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)
DOCKER_MEM_GB=$(awk "BEGIN {printf \"%.1f\", $DOCKER_MEM_BYTES/1073741824}")
note "Docker memory: ${DOCKER_MEM_GB}GB"
if (( $(awk "BEGIN {print ($DOCKER_MEM_GB < 3.5)}") )); then
  echo "WARNING: under 3.5GB available to Docker. The cropped build should" >&2
  echo "         still fit, but raise memory in .wslconfig if it is killed." >&2
fi

# osrm-extract writes several files larger than the input; leave headroom.
AVAIL_KB=$(df -Pk . | awk 'NR==2 {print $4}')
AVAIL_GB=$(awk "BEGIN {printf \"%.1f\", $AVAIL_KB/1048576}")
note "Disk free: ${AVAIL_GB}GB"
if (( $(awk "BEGIN {print ($AVAIL_GB < 4)}") )); then
  echo "Need at least 4GB free to build safely. Clear space and retry." >&2
  exit 1
fi

mkdir -p "$DATA_DIR"

# --- 1. Download ------------------------------------------------------------

step "1/4  Source extract"

if [[ $REDOWNLOAD -eq 1 ]]; then
  rm -f "$SOURCE_PBF"
fi

if [[ -f "$SOURCE_PBF" ]]; then
  note "already present ($(du -h "$SOURCE_PBF" | cut -f1)), skipping download"
else
  note "downloading from geofabrik.de (~240MB)"
  # -C - resumes a partial download rather than starting over.
  curl -L -C - --fail --progress-bar -o "$SOURCE_PBF" "$PBF_URL"
  note "downloaded $(du -h "$SOURCE_PBF" | cut -f1)"
fi

# --- 2. Crop ----------------------------------------------------------------

step "2/4  Crop to Klang Valley bounding box"

if [[ -f "$CROPPED_PBF" && $FORCE -eq 0 ]]; then
  note "already cropped ($(du -h "$CROPPED_PBF" | cut -f1)), skipping"
else
  docker compose --profile build-osrm run --rm osm-crop
  note "cropped to $(du -h "$CROPPED_PBF" | cut -f1)"
fi

# --- 3-4. OSRM MLD pipeline -------------------------------------------------
#
# Three stages, and they must run in this order:
#
#   osrm-extract    reads the PBF, applies the car profile (speed limits,
#                   turn restrictions, which ways are drivable) and builds
#                   the routing graph. The expensive stage.
#   osrm-partition  divides the graph into cells for the multi-level
#                   Dijkstra algorithm.
#   osrm-customize  computes the shortest paths within and between cells.
#                   This is the one you re-run if only speeds change.

step "3/4  osrm-extract (the slow one)"

if [[ -f "$GRAPH" && $FORCE -eq 0 ]]; then
  note "graph already built, skipping. Use --force to rebuild."
else
  docker compose --profile build-osrm run --rm osrm-extract

  step "4/4  osrm-partition + osrm-customize"
  docker compose --profile build-osrm run --rm osrm-partition
  docker compose --profile build-osrm run --rm osrm-customize
fi

# --- Done -------------------------------------------------------------------

step "Built"
du -ch "$DATA_DIR"/klang-valley.osrm* 2>/dev/null | tail -1 | \
  sed 's/^/    graph size: /'
note ""
note "Start the router with:"
note "  docker compose --profile osrm up -d osrm"
note ""
note "Then verify against known routes:"
note "  docker compose exec api python -m routing.verify"
