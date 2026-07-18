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
      print -- 'usage: ./benchmark-vim.sh [--runs N]'
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

script_dir=${0:A:h}
vimrc="$script_dir/vim/.vimrc"
vim_bin=${VIM_BIN:-${commands[vim]:-}}
[[ -n $vim_bin && -x $vim_bin ]] || {
  print -u2 -- 'error: vim is unavailable'
  exit 69
}
[[ -r $vimrc ]] || {
  print -u2 -- "error: Vim configuration is unavailable: $vimrc"
  exit 66
}

work_dir=$(mktemp -d "${TMPDIR:-/tmp}/dotfiles-vim-benchmark.XXXXXX")
cleanup() {
  rm -rf -- "$work_dir"
}
trap cleanup EXIT HUP INT TERM

export XDG_STATE_HOME="$work_dir/state"
sample_file="$work_dir/sample.js"
cat > "$sample_file" <<'JAVASCRIPT'
class Counter {
  constructor(initial = 0) {
    this.value = initial;
  }

  increment() {
    this.value += 1;
    return this.value;
  }
}

const counter = new Counter();
console.log(counter.increment());
JAVASCRIPT

measure_startup() {
  local scenario=$1 start elapsed median p95_index p95
  local -a arguments samples sorted
  shift
  arguments=("$@")

  "$vim_bin" --not-a-term -Nu "$vimrc" -n -i NONE \
    "${arguments[@]}" -c 'qa!' >/dev/null 2>&1
  for (( sample = 1; sample <= runs; ++sample )); do
    start=$EPOCHREALTIME
    "$vim_bin" --not-a-term -Nu "$vimrc" -n -i NONE \
      "${arguments[@]}" -c 'qa!' >/dev/null 2>&1
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
  printf 'startup editor=vim scenario=%s runs=%d median_ms=%.1f p95_ms=%.1f\n' \
    "$scenario" "$runs" "$median" "$p95"
}

measure_startup empty
measure_startup javascript "$sample_file"

smoke_errors="$work_dir/smoke-errors.txt"
smoke_script="$work_dir/smoke.vim"
export DOTFILES_VIM_SMOKE_ERRORS=$smoke_errors
cat > "$smoke_script" <<'SMOKE'
set columns=180 lines=30
call assert_equal('solarized', get(g:, 'colors_name', ''))
call assert_equal(2, exists(':Files'))
call assert_equal(2, exists(':Rg'))
call assert_equal(2, exists(':NERDTreeToggle'))
call assert_equal(2, exists(':QuickRun'))
call assert_equal(2, exists(':Git'))
call assert_equal(2, exists(':ALEInfo'))
call assert_equal(1, get(g:, 'loaded_youcompleteme', 0))
call assert_equal(1, exists('*DotfilesStatusline'))
call assert_equal(0, get(g:, 'loaded_airline', 0))
call assert_equal('%!DotfilesStatusline()', &statusline)
call assert_equal(':Files<CR>', maparg('<C-P>', 'n'))
call assert_equal(1, &backup)
call assert_equal(1, &undofile)
call assert_equal(1, &splitbelow)
call assert_equal(1, &splitright)
call assert_true(isdirectory(&backupdir[:-3]))
call assert_true(isdirectory(&directory[:-3]))
call assert_true(isdirectory(&undodir[:-3]))
call assert_equal('#fdf6e3', synIDattr(hlID('DotStatusA'), 'fg#', 'gui'))
call assert_equal('#93a1a1', synIDattr(hlID('DotStatusA'), 'bg#', 'gui'))
call assert_equal('javascript', &filetype)
call assert_equal('javascript', &syntax)
redraw
let rendered_statusline = ''
for column in range(1, &columns)
  let rendered_statusline .= screenstring(&lines - 1, column)
endfor
call assert_match(' NORMAL ', rendered_statusline)
call assert_match('sample.js', rendered_statusline)
call assert_match('javascript', rendered_statusline)
call assert_match('utf-8\[unix\]', rendered_statusline)
call writefile(v:errors, $DOTFILES_VIM_SMOKE_ERRORS)
if !empty(v:errors)
  cquit
endif
qa!
SMOKE

if ! "$vim_bin" --not-a-term -Nu "$vimrc" -n -i NONE \
  "$sample_file" -S "$smoke_script" >/dev/null 2>&1; then
  print -u2 -- 'error: Vim smoke checks failed'
  [[ ! -s $smoke_errors ]] || cat "$smoke_errors" >&2
  exit 1
fi
print -- 'smoke vim=ok statusline=ok plugins=ok persistent_state=ok'
