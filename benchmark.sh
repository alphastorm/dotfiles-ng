#!/usr/bin/env zsh

emulate -LR zsh
setopt err_exit no_unset pipe_fail

runs=20
while (( $# )); do
  case $1 in
    --runs)
      shift
      (( $# )) || { print -u2 -- 'error: --runs requires a value'; exit 64; }
      runs=$1
      ;;
    --runs=*)
      runs=${1#*=}
      ;;
    -h|--help)
      print -- 'usage: ./benchmark.sh [--runs N]'
      exit 0
      ;;
    *)
      print -u2 -- "error: unknown argument: $1"
      exit 64
      ;;
  esac
  shift
done

[[ $runs == <-> ]] && (( runs >= 5 )) || {
  print -u2 -- 'error: --runs must be an integer of at least 5'
  exit 64
}

zmodload zsh/datetime

preferred_shell=${commands[zsh]:-}
[[ -n $preferred_shell && -x $preferred_shell ]] || {
  print -u2 -- 'error: zsh is unavailable'
  exit 69
}

account_shell=${SHELL:-$preferred_shell}
if [[ $OSTYPE == darwin* && -x /usr/bin/dscl ]]; then
  account_entry=$(
    /usr/bin/dscl . -read "/Users/$USER" UserShell 2>/dev/null || true
  )
  [[ $account_entry != 'UserShell: '* ]] || account_shell=${account_entry#UserShell: }
elif (( $+commands[getent] )); then
  account_entry=$(getent passwd "$USER" 2>/dev/null || true)
  [[ -z $account_entry ]] || account_shell=${account_entry##*:}
fi

shells=($account_shell)
[[ $preferred_shell == $account_shell ]] || shells+=($preferred_shell)
for shell_path in $shells; do
  [[ -x $shell_path ]] || {
    print -u2 -- "error: login shell is not executable: $shell_path"
    exit 69
  }
done

work_dir=$(mktemp -d "${TMPDIR:-/tmp}/dotfiles-benchmark.XXXXXX")
cleanup() {
  rm -rf -- "$work_dir"
}
trap cleanup EXIT HUP INT TERM

measure_startup() {
  local shell_path=$1 start elapsed median p95_index p95
  local -a samples sorted

  "$shell_path" -l -i -c exit >/dev/null 2>&1
  for (( sample = 1; sample <= runs; ++sample )); do
    start=$EPOCHREALTIME
    "$shell_path" -l -i -c exit >/dev/null 2>&1
    elapsed=$(( (EPOCHREALTIME - start) * 1000.0 ))
    samples+=("$elapsed")
  done

  sorted=(${(on)samples})
  if (( runs % 2 )); then
    median=${sorted[$(( runs / 2 + 1 ))]}
  else
    median=$(( (sorted[runs / 2] + sorted[runs / 2 + 1]) / 2.0 ))
  fi
  p95_index=$(( (95 * runs + 99) / 100 ))
  p95=${sorted[$p95_index]}
  printf 'startup shell=%s runs=%d median_ms=%.1f p95_ms=%.1f\n' \
    "$shell_path" "$runs" "$median" "$p95"
}

for shell_path in $shells; do
  measure_startup "$shell_path"
done

profile_dir="$work_dir/profile"
profile_file="$work_dir/zprof.txt"
mkdir -p "$profile_dir"
cat > "$profile_dir/.zshrc" <<PROFILE
zmodload zsh/zprof
source ${(q)HOME}/.zshrc
zprof > ${(q)profile_file}
PROFILE
ZDOTDIR=$profile_dir "$preferred_shell" -i -c exit >/dev/null 2>&1
compinit_calls=$(awk '$NF == "compinit" && $2 ~ /^[0-9]+$/ { print $2; exit }' "$profile_file")
[[ $compinit_calls == 1 ]] || {
  print -u2 -- "error: expected one compinit call, observed ${compinit_calls:-none}"
  exit 1
}
print -- 'smoke compinit_calls=1'

shell_probe='[[ -n $TTY ]] || exit 80
[[ -o zle ]] || exit 81
(( $+functions[_zplug] && $+functions[_omp] && $+functions[_zshz] )) || exit 82
[[ $(bindkey "^T") == *fzf-file-widget* ]] || exit 83
[[ $(bindkey "^R") == *fzf-history-widget* ]] || exit 84
[[ $(bindkey -M emacs "^?") == *backward-delete-char* ]] || exit 85
[[ $(bindkey -M emacs "^H") == *backward-delete-char* ]] || exit 86
[[ $(bindkey -M viins "^?") == *vi-backward-delete-char* ]] || exit 87
[[ $(bindkey -M viins "^H") == *vi-backward-delete-char* ]] || exit 88
print -r -- DOTFILES_SHELL_SMOKE=ok'
# Isolate zpty because pseudo-terminal shutdown sends HUP to its parent.
for shell_path in $shells; do
  if ! (
    trap - EXIT INT TERM
    trap '' HUP
    zmodload zsh/zpty
    zpty dotfiles-smoke ${(q)shell_path} -l -i -c ${(q)shell_probe} ||
      exit 1
    probe_output=
    zpty -rm dotfiles-smoke probe_output '*DOTFILES_SHELL_SMOKE=ok*'
  ); then
    print -u2 -- "error: shell smoke failed for $shell_path"
    exit 1
  fi
done
print -- "smoke completions=ok zle=ok backspace=ok shells=${#shells}"

cache_dir="$work_dir/evalcache"
probe_command="$work_dir/evalcache-probe"
mkdir -p "$cache_dir"
cat > "$probe_command" <<'PROBE'
#!/bin/sh
sleep 0.05
printf '%s\n' 'typeset -g DOTFILES_EVALCACHE_SMOKE=ready'
PROBE
chmod 700 "$probe_command"

pids=()
for (( worker = 1; worker <= 8; ++worker )); do
  ZSH_EVALCACHE_DIR=$cache_dir DOTFILES_EVALCACHE_PROBE=$probe_command \
    "$preferred_shell" -i -c \
      '_evalcache benchmark "$DOTFILES_EVALCACHE_PROBE"' \
      >/dev/null 2>&1 &
  pids+=($!)
done
worker_status=0
for worker_pid in $pids; do
  wait "$worker_pid" || worker_status=1
done
(( worker_status == 0 )) || {
  print -u2 -- 'error: concurrent evalcache workers failed'
  exit 1
}
cache_files=("$cache_dir"/init-benchmark.sh(N))
temp_files=("$cache_dir"/.evalcache-tmp-*(N))
(( ${#cache_files} == 1 && ${#temp_files} == 0 )) || {
  print -u2 -- 'error: evalcache did not publish exactly one complete cache'
  exit 1
}
[[ $(<"$cache_files[1]") == *'DOTFILES_EVALCACHE_SMOKE=ready'* ]] || {
  print -u2 -- 'error: evalcache published incomplete output'
  exit 1
}
print -- 'smoke evalcache_atomicity=ok workers=8'
print -- "smoke account_shell=$account_shell preferred_shell=$preferred_shell"
