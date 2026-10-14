#!/bin/bash
# Pinned, unmodified upstream OMP. All runtime writes are beneath HOME.
# Normal setup stages/selects only; activation of the ambient omp is explicit.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: install-stock-omp.sh [--stage | --check | --select VERSION | --rollback] [--activate-bin]
  (default)       Stage and select the committed stock-runtime.json pin.
  --stage         Download/verify the pin without changing current or ~/.local/bin/omp.
  --check         Check the pinned staged binary's presence, mode and hash; write nothing.
  --select VER    Verify a staged version and select it; preserve current as previous.
  --rollback      Verify/select previous, swapping the current and previous links.
  --activate-bin  Explicitly replace ~/.local/bin/omp with an atomic stock symlink;
                  back up an existing regular launcher under omp-stock/backup/.
  --help          Show this help.
OMP_STOCK_PIN may name a different pin (including file:// release fixtures).
Each staged version retains its pin so older versions can be verified on rollback.
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

action=install
explicit_action=
activate=
requested_version=
while [ "$#" -gt 0 ]; do
  case $1 in
    --stage | --check | --rollback | --select)
      [ -z "$explicit_action" ] || die "choose only one install action"
      explicit_action=1
      action=${1#--}
      if [ "$action" = select ]; then
        [ "$#" -ge 2 ] || die "--select requires a version"
        requested_version=$2
        shift
      fi
      ;;
    --activate-bin) activate=1 ;;
    --help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done
if [ -n "$activate" ] && { [ "$action" = check ] || [ "$action" = stage ]; }; then
  die "--activate-bin requires an install, select, or rollback action"
fi

[ -n "${HOME:-}" ] && [[ "$HOME" = /* ]] || die "HOME must be an absolute directory"
script_dir=$(cd "$(dirname "$0")" && pwd)
pin=${OMP_STOCK_PIN:-"$script_dir/../omp/.omp/stock-runtime.json"}
root="$HOME/.local/opt/omp-stock"
os=$(uname -s)
case "$os:$(uname -m)" in
  Darwin:arm64) platform=darwin-arm64 ;;
  Linux:aarch64 | Linux:arm64) platform=linux-arm64 ;;
  *) die "no stock runtime pin for this platform" ;;
esac
command -v jq >/dev/null 2>&1 || die "jq is required to read the stock runtime pin"

validate_version() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+][A-Za-z0-9.-]+)?$ ]] || die "invalid version: $1"
}

load_pin() {
  local fields
  fields=$(jq -er --arg platform "$platform" '
    [.version, .release_url, .platforms[$platform].asset, .platforms[$platform].sha256]
    | if all(.[]; type == "string" and length > 0 and (test("\\s") | not))
      then @tsv else error("missing or invalid stock runtime pin fields") end
  ' "$1") || die "cannot read stock runtime pin: $1"
  IFS=$'\t' read -r version release_url asset sha256 <<< "$fields"
  validate_version "$version"
  [[ "$asset" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid release asset name"
  [[ "$sha256" =~ ^[a-f0-9]{64}$ ]] || die "invalid pinned checksum"
  case $release_url in
    https://* | file://*) ;;
    *) die "release_url must use https:// (or file:// for local fixtures)" ;;
  esac
}

# Match setup.sh's portable checksum convention.
verify_sha256() {
  local expected=$1 file=$2
  [ -f "$file" ] && ! [ -L "$file" ] || return 1
  if command -v shasum >/dev/null 2>&1; then
    printf '%s  %s\n' "$expected" "$file" | shasum -a 256 --check >/dev/null 2>&1
  elif command -v sha256sum >/dev/null 2>&1; then
    printf '%s  %s\n' "$expected" "$file" | sha256sum -c >/dev/null 2>&1
  else
    die "sha256sum or shasum is required to verify stock OMP"
  fi
}

load_pin "$pin"
pinned_version=$version
temporary_dir=
cleanup() {
  if [ -n "$temporary_dir" ]; then
    rm -rf -- "$temporary_dir"
  fi
}
trap cleanup EXIT

stage() {
  local destination="$root/$version"
  ! [ -L "$destination" ] || die "refusing a linked version directory: $destination"
  if [ -x "$destination/omp" ] && verify_sha256 "$sha256" "$destination/omp" &&
     cmp -s "$pin" "$destination/stock-runtime.json"; then
    return 0
  fi
  ! [ -d "$destination/omp" ] || die "refusing a binary directory: $destination/omp"
  mkdir -p "$root"
  temporary_dir=$(mktemp -d "$root/.stage.XXXXXX")
  curl --fail --silent --show-error --location --proto '=https,file' --proto-redir '=https' \
    "${release_url%/}/$asset" --output "$temporary_dir/omp"
  verify_sha256 "$sha256" "$temporary_dir/omp" || die "stock OMP checksum mismatch"
  chmod 0755 "$temporary_dir/omp"
  cp "$pin" "$temporary_dir/stock-runtime.json"
  mkdir -p "$destination"
  mv -f "$temporary_dir/omp" "$destination/omp"
  mv -f "$temporary_dir/stock-runtime.json" "$destination/stock-runtime.json"
  rmdir "$temporary_dir"
  temporary_dir=
}

verify_version() (
  local selected=$1
  validate_version "$selected"
  if [ "$selected" != "$pinned_version" ]; then
    load_pin "$root/$selected/stock-runtime.json"
  fi
  [ "$version" = "$selected" ] || die "staged pin does not name version $selected"
  ! [ -L "$root/$selected" ] || die "refusing a linked version directory"
  [ -x "$root/$selected/omp" ] || die "missing executable: $root/$selected/omp"
  verify_sha256 "$sha256" "$root/$selected/omp" || die "stock OMP checksum mismatch: $selected"
)

link_version() {
  local link=$1 target
  if [ -L "$link" ]; then
    target=$(readlink "$link")
    validate_version "$target"
    printf '%s\n' "$target"
  elif [ -e "$link" ]; then
    die "refusing to replace a non-symlink selector: $link"
  fi
}

atomic_link() {
  local target=$1 link=$2 directory
  if [ -L "$link" ] && [ "$(readlink "$link")" = "$target" ]; then
    return 0
  fi
  directory=$(dirname "$link")
  mkdir -p "$directory"
  temporary_dir=$(mktemp -d "$directory/.omp-link.XXXXXX")
  ln -s "$target" "$temporary_dir/link"
  # mv normally follows a destination symlink to a directory. Both variants
  # below rename over the link itself, without an unlink/relink gap.
  if [ "$os" = Darwin ]; then
    mv -fh "$temporary_dir/link" "$link"
  else
    mv -fT "$temporary_dir/link" "$link"
  fi
  rmdir "$temporary_dir"
  temporary_dir=
}

select_version() {
  local selected=$1 current
  verify_version "$selected"
  current=$(link_version "$root/current")
  [ "$current" != "$selected" ] || return 0
  if [ -n "$current" ]; then
    link_version "$root/previous" >/dev/null
    atomic_link "$current" "$root/previous"
  fi
  atomic_link "$selected" "$root/current"
}

activate_bin() {
  local bin="$HOME/.local/bin/omp" target="$root/current/omp" backup suffix=0
  if [ -L "$bin" ] && [ "$(readlink "$bin")" = "$target" ]; then
    return 0
  fi
  if [ -e "$bin" ] && ! [ -L "$bin" ]; then
    [ -f "$bin" ] || die "refusing to replace a non-file launcher: $bin"
    mkdir -p "$root/backup"
    backup="$root/backup/omp.$(date -u +%Y%m%dT%H%M%SZ)"
    while [ -e "$backup" ] || [ -L "$backup" ]; do
      suffix=$((suffix + 1))
      backup="$root/backup/omp.$(date -u +%Y%m%dT%H%M%SZ).$suffix"
    done
    # Copy first: the original launcher stays usable until the atomic rename.
    cp -p "$bin" "$backup"
  fi
  atomic_link "$target" "$bin"
}

case $action in
  check) verify_version "$pinned_version" ;;
  stage) stage ;;
  select) select_version "$requested_version" ;;
  rollback)
    requested_version=$(link_version "$root/previous")
    [ -n "$requested_version" ] || die "no previous stock version to roll back to"
    select_version "$requested_version"
    ;;
  install) stage; select_version "$pinned_version" ;;
esac
if [ -n "$activate" ]; then
  activate_bin
fi
