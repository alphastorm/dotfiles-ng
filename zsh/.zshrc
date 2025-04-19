# Enable Powerlevel10k instant prompt. Should stay close to the top of ~/.zshrc.
# Initialization code that may require console input (password prompts, [y/n]
# confirmations, etc.) must go above this block; everything else may go below.
if [[ -r "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh" ]]; then
  source "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh"
fi

source ~/.zplug/init.zsh

# color
export TERM='xterm-256color'

# languages
export LANG='en_US.UTF-8'
export LANGUAGE='en_US:en'
export LC_ALL='en_US.UTF-8'

# let zplug manage zplug
zplug 'zplug/zplug', hook-build:'zplug --self-manage'

# essential plugins
zplug 'agkozak/zsh-z'
zplug 'mroth/evalcache'
zplug 'romkatv/powerlevel10k', as:theme, depth:1
zplug 'seebi/dircolors-solarized'
zplug 'so-fancy/diff-so-fancy', as:command, use:diff-so-fancy

# prezto modules
zplug 'modules/ssh', from:prezto

# fzf settings and integration with z
export FZF_DEFAULT_COMMAND='rg -i --files --hidden --follow --glob "!.git/*" --glob "!.DS_Store/*" --glob "!node_modules/*" --glob "!env/*"'
unalias z 2> /dev/null
z() {
  [ $# -gt 0 ] && _z "$*" && return
  cd "$(_z -l 2>&1 | fzf --height 40% --reverse --inline-info +s --tac --query "$*" | sed 's/^[0-9,.]* *//')"
}

# bat settings
export BAT_THEME='Solarized (dark)'

# install plugins if there are plugins that have not been installed
if ! zplug check; then
    printf 'Install? [y/N]: '
    if read -q; then
        echo; zplug install
    fi
fi

# then, source plugins and add commands to $path
zplug load

# load zsh config files
config_files=(~/.zsh/**/*.zsh(N))
for file in ${config_files}
do
  source $file
done

_evalcache dircolors ~/.zplug/repos/seebi/dircolors-solarized/dircolors.256dark

# load after zplug to override
[ -f ~/.fzf.zsh ] && source ~/.fzf.zsh

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

# load pyenv
export PYENV_ROOT="$HOME/.pyenv"
command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"
_evalcache pyenv init -

# load pyenv-virtualenv
_evalcache pyenv virtualenv-init - | sed s/precmd/precwd/g

# pnpm
export PNPM_HOME="/Users/srs/Library/pnpm"
case ":$PATH:" in
  *":$PNPM_HOME:"*) ;;
  *) export PATH="$PNPM_HOME:$PATH" ;;
esac
# pnpm end

# To customize prompt, run `p10k configure` or edit ~/.p10k.zsh.
[[ ! -f ~/.p10k.zsh ]] || source ~/.p10k.zsh
