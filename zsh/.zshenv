# ensure that a non-login, non-interactive shell has a defined environment.
if [[ ( "$SHLVL" -eq 1 && ! -o LOGIN ) && -s "${ZDOTDIR:-$HOME}/.zprofile" ]]; then
  source "${ZDOTDIR:-$HOME}/.zprofile"
fi

# load zsh config files
env_config_files=(~/.zsh/**/*.zshenv(N))
if test ! -z "$env_config_files" ;
then
  for file in ${env_config_files}
  do
    source $file
  done
fi
