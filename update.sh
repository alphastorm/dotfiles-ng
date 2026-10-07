#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")" || exit
git pull --ff-only
# setup.sh stows the private checkout too, so a host that pulls only this one
# installs a stale half. Its runtime-worktree branches sync at OMP session start.
private_dir=${DOTFILES_PRIVATE_DIR:-"$HOME/.dotfiles-private"}
if [ -d "$private_dir/.git" ]; then
  git -C "$private_dir" pull --ff-only
fi
./setup.sh
