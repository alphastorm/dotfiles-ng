#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")" || exit
# shellcheck source=setup.sh
source ./setup.sh
# --no-rebase: the global pull.rebase=true would refuse any unstaged change,
# even one the fast-forward never touches.
git pull --ff-only --no-rebase
# setup.sh stows the private checkout too, so a host that pulls only this one
# installs a stale half. Runtime branches converge in setup.sh below and in
# interactive OMP sessions at start and every 15 minutes.
private_dir=${DOTFILES_PRIVATE_DIR:-"$HOME/.dotfiles-private"}
if is_git_root "$private_dir"; then
  git -C "$private_dir" pull --ff-only --no-rebase
elif [ -e "$private_dir" ] || [ -L "$private_dir" ]; then
  echo "error: private dotfiles path is not a Git repository root: $private_dir" >&2
  exit 1
fi
./setup.sh
