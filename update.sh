#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")" || exit
git pull --ff-only
./setup.sh
