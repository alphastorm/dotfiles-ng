#!/bin/bash
cd "$(dirname "$0")" || exit
PULL_RESULT=$(git pull)
[ "$PULL_RESULT" = "Already up-to-date." ] && ./setup.sh
