# The following lines were added by Docker Desktop to add commands to your PATH.
export PATH="$PATH:/Users/srs/.docker/bin"
# End of Docker Desktop section.

# browser
if [[ "$OSTYPE" == darwin* ]]; then
  export BROWSER='open'
fi

# editors
export EDITOR='vim'
export VISUAL='vim'
export PAGER='less'

# gpg
[[ -n $TTY ]] && export GPG_TTY=$TTY

# ensure path arrays do not contain duplicates.
typeset -gU cdpath mailpath

# set the list of directories that zsh searches for programs.
path=(
  /opt/homebrew/{bin,sbin}
  /usr/local/{bin,sbin}
  $path
)

# set the default less options.
# mouse-wheel scrolling has been disabled by -x (disable screen clearing).
# remove -x and -f (exit if the content fits on one screen) to enable it.
export LESS='-F -g -i -M -R -S -w -X -z-4'

# set the less input preprocessor.
# try both `lesspipe` and `lesspipe.sh` as either might exist on a system.
if (( $#commands[(i)lesspipe(|.sh)] )); then
  export LESSOPEN="| /usr/bin/env $commands[(i)lesspipe(|.sh)] %s 2>&-"
fi
