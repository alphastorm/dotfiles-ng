# Enable Powerlevel10k instant prompt. Should stay close to the top of ~/.zshrc.
# Initialization code that may require console input (password prompts, [y/n]
# confirmations, etc.) must go above this block; everything else may go below.
if [[ -r "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh" ]]; then
  source "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh"
fi

# Homebrew-generated OMP completion; compinit runs after plugin paths are added.
if [[ -r /opt/homebrew/share/zsh/site-functions/_omp ]]; then
  fpath=(/opt/homebrew/share/zsh/site-functions $fpath)
fi

fpath=("$HOME/.zplug/misc/completions" $fpath)
autoload -Uz _zplug


# fzf settings and integration with z
export FZF_DEFAULT_COMMAND='rg -i --files --hidden --follow --glob "!.git/*" --glob "!.DS_Store/*" --glob "!node_modules/*" --glob "!env/*"'

# bat settings
export BAT_THEME='Solarized (dark)'

# Load plugins directly; zplug itself is loaded only for explicit management.
_zsh_plugin_root="$HOME/.zplug/repos"
fpath=("$_zsh_plugin_root/agkozak/zsh-z" $fpath)
path=("$_zsh_plugin_root/so-fancy/diff-so-fancy" $path)

_zsh_source_plugin() {
  if [[ ! -r $1 ]]; then
    print -u2 -r -- "missing Zsh plugin: $1; run setup.sh"
    return 1
  fi
  source "$1"
}

_zsh_source_plugin "$_zsh_plugin_root/agkozak/zsh-z/zsh-z.plugin.zsh"
_zsh_source_plugin "$_zsh_plugin_root/sorin-ionescu/prezto/modules/ssh/init.zsh"
_zsh_source_plugin \
  "$_zsh_plugin_root/romkatv/powerlevel10k/powerlevel10k.zsh-theme"
unfunction _zsh_source_plugin
unset _zsh_plugin_root

autoload -Uz compinit
compinit -d "$HOME/.zplug/zcompdump"

zplug() {
  unfunction zplug
  export ZPLUG_LOADFILE="$HOME/.zplugrc"
  source "$HOME/.zplug/init.zsh" || return
  zplug "$@"
}

export ZSH_EVALCACHE_DIR=${ZSH_EVALCACHE_DIR:-"$HOME/.zsh-evalcache"}

_evalcache() {
  if (( $# == 0 )); then
    print -u2 -r -- 'evalcache: cache identifier is required'
    return 64
  fi

  local cache_id=$1
  shift
  if [[ -z $cache_id ||
        $cache_id != ${cache_id//[^A-Za-z0-9._-]/} ]]; then
    print -u2 -r -- "evalcache: invalid cache identifier: $cache_id"
    return 64
  fi

  local name argument
  for argument in "$@"; do
    if [[ $argument == ${argument#[A-Za-z_][A-Za-z0-9_]*=} ]]; then
      name=$argument
      break
    fi
  done

  if [[ -z $name ]]; then
    print -u2 -r -- 'evalcache: initializer command is required'
    return 64
  fi

  if [[ $ZSH_EVALCACHE_DISABLE == true ]]; then
    local live_output initializer_status
    live_output=$(eval ${(q)@})
    initializer_status=$?
    (( initializer_status == 0 )) || return $initializer_status
    eval "$live_output"
    return $?
  fi

  local data="${(qqq)@}"
  local function_definition=
  if function_definition=$(typeset -f "$name" 2>/dev/null); then
    data+=$'\n'"$function_definition"
  fi

  local metadata="# evalcache-key: ${(qqqq)data}"
  local cache_file="$ZSH_EVALCACHE_DIR/init-${cache_id}.sh"
  local cached_metadata=
  if [[ -s $cache_file ]]; then
    IFS= read -r cached_metadata < "$cache_file"
    if [[ $cached_metadata == "$metadata" ]]; then
      source "$cache_file"
      return $?
    fi
  fi

  if ! type "$name" >/dev/null 2>&1; then
    print -u2 -r -- "evalcache: $name is not installed or in PATH"
    return 127
  fi

  print -u2 -r -- "evalcache: caching output of ${(qqq)@}"
  command mkdir -p "$ZSH_EVALCACHE_DIR" || return

  local temporary_file initializer_status publish_status
  temporary_file=$(
    command mktemp \
      "$ZSH_EVALCACHE_DIR/.evalcache-tmp-${cache_id}.XXXXXX"
  ) || return

  print -r -- "$metadata" >| "$temporary_file"
  eval ${(q)@} >> "$temporary_file"
  initializer_status=$?
  if (( initializer_status != 0 )); then
    command rm -f "$temporary_file"
    return $initializer_status
  fi

  command mv -f "$temporary_file" "$cache_file"
  publish_status=$?
  if (( publish_status != 0 )); then
    command rm -f "$temporary_file"
    return $publish_status
  fi

  source "$cache_file"
}

_evalcache_clear() {
  command rm -f \
    "$ZSH_EVALCACHE_DIR"/init-*.sh(N) \
    "$ZSH_EVALCACHE_DIR"/init-*.zwc(N) \
    "$ZSH_EVALCACHE_DIR"/.evalcache-tmp-*(N)
}

if zmodload zsh/stat 2>/dev/null; then
  _shell_cache_key() {
    local -A file_stat
    zstat -H file_stat -- "$1" || return 1
    REPLY="${1:A}:${file_stat[mtime]}:${file_stat[size]}:${file_stat[inode]}"
  }
else
  _shell_cache_key() { return 1 }
fi

# load zsh config files
config_files=(~/.zsh/**/*.zsh(N))
for file in ${config_files}
do
  source $file
done

_dircolors_file="$HOME/.zplug/repos/seebi/dircolors-solarized/dircolors.256dark"
_gdircolors_command_key=
_gdircolors_data_key=
_dircolors_cache_id=${TERM:-unknown}
_dircolors_cache_id=${_dircolors_cache_id//[^A-Za-z0-9._-]/_}
_dircolors_cache_id="gdircolors-$_dircolors_cache_id"
if (( $+commands[gdircolors] )) && [[ -r $_dircolors_file ]]; then
  _shell_cache_key "$commands[gdircolors]" && _gdircolors_command_key=$REPLY
  _shell_cache_key "$_dircolors_file" && _gdircolors_data_key=$REPLY
  if [[ -n $_gdircolors_command_key && -n $_gdircolors_data_key ]]; then
    _evalcache \
      "$_dircolors_cache_id" \
      "TERM=${TERM:-}" \
      "GDIRCOLORS_COMMAND_KEY=$_gdircolors_command_key" \
      "GDIRCOLORS_DATA_KEY=$_gdircolors_data_key" \
      gdircolors "$_dircolors_file"
  else
    _evalcache "$_dircolors_cache_id" \
      "TERM=${TERM:-}" gdircolors "$_dircolors_file"
  fi
fi
unset _dircolors_file _dircolors_cache_id
unset _gdircolors_command_key _gdircolors_data_key

# Load after zplug to override its widget bindings.
# fzf restores options with eval; ZLE cannot be re-enabled without a TTY.
[[ -t 0 ]] || unsetopt zle
if [[ -r "$HOME/.fzf/shell/key-bindings.zsh" &&
      -r "$HOME/.fzf/shell/completion.zsh" ]]; then
  path+=("$HOME/.fzf/bin")
  source "$HOME/.fzf/shell/key-bindings.zsh"
  source "$HOME/.fzf/shell/completion.zsh"
elif [[ -x "$HOME/.fzf/bin/fzf" && -r "$HOME/.fzf.zsh" ]]; then
  source "$HOME/.fzf.zsh"
fi

unalias z 2>/dev/null
if (( $+functions[zshz] )); then
  z() {
    if (( $# )); then
      zshz "$@"
      return $?
    fi

    if (( ! $+commands[fzf] )); then
      zshz -l
      return $?
    fi

    local selected
    selected=$(
      zshz -l 2>/dev/null |
        command sed '/^common:[[:space:]]/d' |
        command fzf --height 40% --reverse --inline-info +s --tac |
        command sed 's/^[0-9,.]*[[:space:]]*//'
    )
    [[ -n $selected ]] || return 0
    builtin cd -- "$selected"
  }
  compdef _zshz z
fi

# zsh settings
# ignoreeof forces the user to type exit or logout, instead of just pressing ^d
setopt ignoreeof

# execute the line directly when performing history expansion, instead of
# performing the expansion and then reloading the line into the buffer
setopt nohistverify

# don't print an error if a pattern for filename generation has no matches
# fixes adding files to git with wildcards (e.g. via gaa)
unsetopt NOMATCH

# aliases
alias buu='brew update && brew upgrade'
alias ls='lsd'
alias v='vim'
alias vu='vim +PlugUpgrade +PlugUpdate +qa!'
alias ..='cd ..'

# git aliases
alias gaa='git add --all'
alias gc='git commit --verbose'
alias gca='git commit --amend --verbose'
alias gco='git checkout'
alias gd='git diff'
alias gl='git l'
alias gp='git pull'
alias gpr='git pull --rebase --autostash'
alias gr='git r'
alias gs='git status'

grm() {
  local current_branch default_branch branch
  current_branch=$(git branch --show-current) || return
  default_branch=$(
    git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null
  )
  default_branch=${default_branch#origin/}
  if [[ -z $default_branch ]] ||
     ! git show-ref --verify --quiet "refs/heads/$default_branch"; then
    default_branch=$current_branch
  fi

  while IFS= read -r branch; do
    [[ $branch == "$current_branch" ||
       $branch == "$default_branch" ]] && continue
    git branch -d -- "$branch" || return
  done < <(
    git for-each-ref --format='%(refname:short)' \
      --merged "$default_branch" refs/heads
  )
}

gum() {
  local current_branch upstream remote
  current_branch=$(git branch --show-current) || return
  upstream=$(
    git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}'
  ) || return
  remote=${upstream%%/*}

  print -r -- "Resetting ${current_branch:-HEAD} to $upstream..."
  git fetch --prune "$remote" || return
  git reset --hard "$upstream"
}

# history settings
alias history='fc -il -200'
export HISTSIZE=100000
export HISTFILE="$HOME/.history"
export SAVEHIST=$HISTSIZE

# load pyenv and pyenv-virtualenv
export PYENV_ROOT="$HOME/.pyenv"
(( $+commands[pyenv] )) || path=("$PYENV_ROOT/bin" $path)
_pyenv_command_key=
if (( $+commands[pyenv] )); then
  if _shell_cache_key "$commands[pyenv]"; then
    _pyenv_command_key=$REPLY
    _evalcache pyenv-init "PYENV_INIT_KEY=$_pyenv_command_key" \
      pyenv init --no-push-path --no-rehash - zsh
  else
    _evalcache pyenv-init pyenv init --no-push-path --no-rehash - zsh
  fi

  if (( $+commands[pyenv-virtualenv-init] )); then
    if [[ -n $_pyenv_command_key ]] &&
       _shell_cache_key "$commands[pyenv-virtualenv-init]"; then
      _evalcache \
        pyenv-virtualenv-init \
        "PYENV_VIRTUALENV_INIT_KEY=${_pyenv_command_key}:$REPLY" \
        pyenv virtualenv-init -
    else
      _evalcache pyenv-virtualenv-init pyenv virtualenv-init -
    fi
  fi
fi
unset _pyenv_command_key
unfunction _shell_cache_key

# pnpm
if [[ -z ${PNPM_HOME:-} ]]; then
  if [[ $OSTYPE == darwin* ]]; then
    export PNPM_HOME="$HOME/Library/pnpm"
  else
    export PNPM_HOME="$HOME/.local/share/pnpm"
  fi
fi
path=("$PNPM_HOME" $path)
# pnpm end


# oh-my-pi
export PI_BASH_NO_LOGIN=1

# To customize prompt, run `p10k configure` or edit ~/.p10k.zsh.
[[ ! -f ~/.p10k.zsh ]] || source ~/.p10k.zsh
