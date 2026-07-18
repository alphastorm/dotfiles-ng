# Enable Powerlevel10k instant prompt. Should stay close to the top of ~/.zshrc.
# Initialization code that may require console input (password prompts, [y/n]
# confirmations, etc.) must go above this block; everything else may go below.
if [[ -r "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh" ]]; then
  source "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh"
fi

# Homebrew-generated OMP completion; zplug runs compinit below.
if [[ -r /opt/homebrew/share/zsh/site-functions/_omp ]]; then
  fpath=(/opt/homebrew/share/zsh/site-functions $fpath)
fi

source ~/.zplug/init.zsh


# languages
export LANG='en_US.UTF-8'
export LANGUAGE='en_US:en'
export LC_ALL='en_US.UTF-8'

# let zplug manage zplug
zplug 'zplug/zplug', hook-build:'zplug --self-manage'

# essential plugins
zplug 'agkozak/zsh-z'
zplug 'mroth/evalcache'
zplug 'romkatv/powerlevel10k', as:theme, depth:1, use:'powerlevel10k.zsh-theme'
zplug 'seebi/dircolors-solarized'
zplug 'so-fancy/diff-so-fancy', as:command, use:diff-so-fancy

# prezto modules
zplug 'modules/ssh', from:prezto

# fzf settings and integration with z
export FZF_DEFAULT_COMMAND='rg -i --files --hidden --follow --glob "!.git/*" --glob "!.DS_Store/*" --glob "!node_modules/*" --glob "!env/*"'

# bat settings
export BAT_THEME='Solarized (dark)'

# then, source plugins and add commands to $path
zplug load

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
if (( $+commands[gdircolors] )) && [[ -r $_dircolors_file ]]; then
  _shell_cache_key "$commands[gdircolors]" && _gdircolors_command_key=$REPLY
  _shell_cache_key "$_dircolors_file" && _gdircolors_data_key=$REPLY
  if [[ -n $_gdircolors_command_key && -n $_gdircolors_data_key ]]; then
    _evalcache \
      "GDIRCOLORS_COMMAND_KEY=$_gdircolors_command_key" \
      "GDIRCOLORS_DATA_KEY=$_gdircolors_data_key" \
      gdircolors "$_dircolors_file"
  else
    _evalcache gdircolors "$_dircolors_file"
  fi
fi
unset _dircolors_file _gdircolors_command_key _gdircolors_data_key

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
alias gpr='git pull --rebase origin master'
alias gr='git r'
alias grm='git branch --merged master | grep -v "\* master" | xargs -n 1 git branch -d'
alias gs='git status'
alias gum="echo 'Resetting master to the latest origin/master...' && git fetch && git update-ref refs/heads/master origin/master"

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
    _evalcache "PYENV_INIT_KEY=$_pyenv_command_key" \
      pyenv init --no-push-path --no-rehash - zsh
  else
    _evalcache pyenv init --no-push-path --no-rehash - zsh
  fi

  if (( $+commands[pyenv-virtualenv-init] )); then
    if [[ -n $_pyenv_command_key ]] &&
       _shell_cache_key "$commands[pyenv-virtualenv-init]"; then
      _evalcache \
        "PYENV_VIRTUALENV_INIT_KEY=${_pyenv_command_key}:$REPLY" \
        pyenv virtualenv-init -
    else
      _evalcache pyenv virtualenv-init -
    fi
  fi
fi
unset _pyenv_command_key
unfunction _shell_cache_key

# pnpm
export PNPM_HOME="$HOME/Library/pnpm"
case ":$PATH:" in
  *":$PNPM_HOME:"*) ;;
  *) export PATH="$PNPM_HOME:$PATH" ;;
esac
# pnpm end

# homebrew vim
path=(/opt/homebrew/{bin,sbin} $path)

# oh-my-pi
export PI_BASH_NO_LOGIN=1

# To customize prompt, run `p10k configure` or edit ~/.p10k.zsh.
[[ ! -f ~/.p10k.zsh ]] || source ~/.p10k.zsh
