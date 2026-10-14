#!/usr/bin/env bash
# Upgrade the pinned Homebrew omp to its tap's latest release, verify it and pin
# it again. One host at a time: your main machine first, the others once a
# session on the new version works there.
set -euo pipefail

formula=can1357/tap/omp
team=VTLHGQC72S # upstream's Developer ID team (Can Boluk)

keg_bin() { echo "$(cd "$(brew --prefix "$formula")/bin" && pwd -P)/omp"; }

brew update --quiet
old_bin=$(keg_bin)
before=$("$old_bin" --version)

# Never leave omp unpinned, even when the upgrade or a check below fails.
trap 'brew pin "$formula"' EXIT
pinned=$(brew list --pinned)
if [[ $'\n'$pinned$'\n' == *$'\n'omp$'\n'* ]]; then
  brew unpin "$formula"
fi
# Keep the old keg: running sessions, the daemon broker and the browser relay
# execute it until they restart, and every worker they spawn needs it on disk.
HOMEBREW_NO_INSTALL_CLEANUP=1 brew upgrade "$formula"

bin=$(keg_bin)
after=$("$bin" --version)
codesign --verify --strict "$bin"
signature=$(codesign -dv "$bin" 2>&1)
if [[ $signature != *"TeamIdentifier=$team"* ]]; then
  echo "error: $bin is not signed by team $team" >&2
  exit 1
fi

# omp keeps running without an extension or plugin that fails to load and says
# so only on stderr, so a startup with the user's config must print nothing there.
smoke=$(mktemp -d)
status=0
errors=$(cd "$smoke" && "$bin" --mode rpc --no-session --no-title </dev/null 2>&1 >/dev/null) || status=$?
rm -rf "$smoke"
if [ "$status" -ne 0 ] || [ -n "$errors" ]; then
  printf 'error: %s failed its startup check (exit %s):\n%s\n' "$after" "$status" "$errors" >&2
  if [ "$bin" != "$old_bin" ]; then
    printf 'roll back to %s: brew unlink omp && install -m 0555 %s ~/.local/bin/omp\n' "$before" "$old_bin" >&2
    echo "undo the rollback: rm ~/.local/bin/omp && brew link omp" >&2
  fi
  exit 1
fi

if [ "$bin" = "$old_bin" ]; then
  echo "$after is the tap's latest; pinned"
else
  echo "$before -> $after; pinned"
fi
stale=$({ lsof -a -c omp -d txt -Fpn 2>/dev/null || true; } |
  awk -v bin="$bin" '/^p/ { pid = substr($0, 2) } /^n.*\/bin\/omp$/ && substr($0, 2) != bin { print pid }' |
  sort -u)
if [ -n "$stale" ]; then
  echo "still running an older omp; restart when convenient (relay and broker: skill omp-browser-relay):"
  ps -o pid=,lstart=,command= -p "$(paste -sd, - <<<"$stale")" | cut -c1-160
fi
