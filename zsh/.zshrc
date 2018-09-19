source ~/.zplug/init.zsh
source ~/.promptline.sh

# color
export TERM='xterm-256color'

# languages
export LANG='en_US.UTF-8'
export LANGUAGE='en_US:en'
export LC_ALL='en_US.UTF-8'

# let zplug manage zplug
zplug 'zplug/zplug', hook-build:'zplug --self-manage'

# essential plugins
zplug 'rupa/z', use:z.sh
zplug 'so-fancy/diff-so-fancy', as:command, use:diff-so-fancy
zplug 'seebi/dircolors-solarized'

# prezto modules
zplug 'modules/environment', from:prezto
zplug 'modules/terminal', from:prezto
zplug 'modules/editor', from:prezto
zplug 'modules/history', from:prezto
zplug 'modules/directory', from:prezto
zplug 'modules/spectrum', from:prezto
zplug 'modules/gnu-utility', from:prezto
zplug 'modules/utility', from:prezto
zplug 'modules/ssh', from:prezto
zplug 'modules/completion', from:prezto
zplug 'modules/screen', from:prezto
zplug 'modules/homebrew', from:prezto, if:"[[ $OSTYPE == *darwin* ]]"
zplug 'modules/python', from:prezto
zplug 'modules/git', from:prezto
zplug 'modules/syntax-highlighting', from:prezto
zplug 'modules/history-substring-search', from:prezto

# prezto settings
zstyle ':prezto:*:*' color 'yes'
zstyle ':prezto:module:editor' key-bindings 'vi'
zstyle ':prezto:module:terminal' auto-title 'yes'
zstyle ':prezto:module:ssh:load' identities 'id_rsa' 'alpha_id_rsa' 'phab_id_rsa'

# go settings
export GOPATH='$HOME/gocode'

# fzf settings and integration with z
export FZF_DEFAULT_COMMAND='rg -i --files --hidden --follow --glob "!.git/*" --glob "!.DS_Store/*" --glob "!node_modules/*" --glob "!env/*"'
unalias z 2> /dev/null
z() {
  [ $# -gt 0 ] && _z "$*" && return
  cd "$(_z -l 2>&1 | fzf --height 40% --reverse --inline-info +s --tac --query "$*" | sed 's/^[0-9,.]* *//')"
}

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

eval `dircolors ~/.zplug/repos/seebi/dircolors-solarized/dircolors.256dark`

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
alias v='vim'
alias vu='vim +PlugUpgrade +PlugUpdate +qa!'

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
