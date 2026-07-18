if [[ -d /opt/homebrew/opt/coreutils/libexec/gnubin ]]; then
  path=(/opt/homebrew/opt/coreutils/libexec/gnubin $path)
elif [[ -d /usr/local/opt/coreutils/libexec/gnubin ]]; then
  path=(/usr/local/opt/coreutils/libexec/gnubin $path)
fi
export HOMEBREW_NO_ANALYTICS=1
export QUOTING_STYLE=literal
