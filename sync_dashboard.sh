#!/bin/bash
# Pull the latest snapshot and drop the dashboard somewhere you can actually open it.
# OneDrive copy means it syncs to your phone and other machines.
set -euo pipefail
REPO="$HOME/dvc-observer"
DEST="$HOME/Library/CloudStorage/OneDrive-Personal/Claude/Personal/Disney"

cd "$REPO"
git pull --quiet --ff-only
if [ -f dashboard.html ]; then
  cp dashboard.html "$DEST/dvc-market-dashboard.html"
  echo "updated $DEST/dvc-market-dashboard.html"
else
  echo "no dashboard.html in repo yet" >&2
fi
