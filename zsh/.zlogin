# execute code that does not affect the current session in the background.
{
  # compile the completion dump to increase startup speed.
  zcompdump="${ZPLUG_HOME:-$HOME/.zplug}/zcompdump"
  if [[ -s "$zcompdump" && (! -s "${zcompdump}.zwc" || "$zcompdump" -nt "${zcompdump}.zwc") ]]; then
    zcompile "$zcompdump"
  fi
} &!
