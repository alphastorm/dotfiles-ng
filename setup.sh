#!/bin/bash
set -euo pipefail

SCRIPTDIR=$(dirname "$0")
cd "$SCRIPTDIR" || exit

# detect platform-dependent options
OS=$(uname -s)
echo "os: $OS"

if [ "$OS" == "Darwin" ]; then
  PLATFORM=osx
  PACKAGE_MANAGER=brew

  function load_brew() {
    local brew_executable

    brew_executable=$(command -v brew 2>/dev/null)
    if ! [ -x "$brew_executable" ]; then
      if [ -x /opt/homebrew/bin/brew ]; then
        brew_executable=/opt/homebrew/bin/brew
      elif [ -x /usr/local/bin/brew ]; then
        brew_executable=/usr/local/bin/brew
      else
        return 1
      fi
    fi

    eval "$("$brew_executable" shellenv)"
  }

  if ! load_brew; then
    HOMEBREW_INSTALLER=$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh) || {
      echo "error: failed to download the Homebrew installer." >&2
      exit 1
    }
    /bin/bash -c "$HOMEBREW_INSTALLER" || {
      echo "error: the Homebrew installer failed." >&2
      exit 1
    }
    unset HOMEBREW_INSTALLER

    if ! load_brew; then
      echo "error: Homebrew is unavailable after installation." >&2
      exit 1
    fi
  fi
elif [ "$OS" == "Linux" ]; then
  PLATFORM=linux
  if [ "$(command -v apt-get)" ]; then
    PACKAGE_MANAGER=apt
  else
    echo 'no package manager found.  install aptitude to continue.'
    exit 1
  fi
else
  echo "unrecognized os: $OS"
  exit 1
fi
echo "platform: $PLATFORM"
echo "package manager: $PACKAGE_MANAGER"

function install_brew_packages() {
  echo "installing homebrew packages..."
  brew update

  brew install \
    bat \
    cmake \
    coreutils \
    csshx \
    eza \
    gpg \
    htop \
    httpie \
    hugo \
    jq \
    lsd \
    node \
    pinentry \
    pinentry-mac \
    pngquant \
    pnpm \
    python-setuptools \
    ripgrep \
    semgrep \
    shellcheck \
    stow \
    tree \
    vim \
    wget \
    zsh

  brew install --cask \
    font-meslo-lg-nerd-font \
    keybase
}

function install_apt_packages() {
  echo "installing aptitude packages..."
  sudo apt update

  sudo apt -y install \
    bsdmainutils \
    build-essential \
    cmake \
    curl \
    gawk \
    git \
    golang \
    jq \
    nodejs \
    python-dev \
    python-pip \
    python3-dev \
    screen \
    shellcheck \
    ssh \
    stow \
    tree \
    unzip \
    vim \
    zsh

  # add nodesource node.js binary distributions
  curl -sL https://deb.nodesource.com/setup_10.x | sudo -E bash -
  sudo apt -y install nodejs

  # install bat if not present
  if ! [ -x "$(command -v bat)" ]; then
    BAT_REPO="https://github.com/sharkdp/bat/releases/download"
    BAT_LATEST=$(curl -sSL "https://api.github.com/repos/sharkdp/bat/releases/latest" | jq --raw-output .tag_name)
    BAT_RELEASE="bat_${BAT_LATEST//v}_amd64.deb"

    curl -LO "${BAT_REPO}/${BAT_LATEST}/${BAT_RELEASE}"
    sudo dpkg -i "${BAT_RELEASE}"
    rm "${BAT_RELEASE}"
  fi

  # install rg if not present
  if ! [ -x "$(command -v rg)" ]; then
    RG_REPO="https://github.com/BurntSushi/ripgrep/releases/download"
    RG_LATEST=$(curl -sSL "https://api.github.com/repos/BurntSushi/ripgrep/releases/latest" | jq --raw-output .tag_name)
    RG_RELEASE="ripgrep_${RG_LATEST}_amd64.deb"

    curl -LO "${RG_REPO}/${RG_LATEST}/${RG_RELEASE}"
    sudo dpkg -i "${RG_RELEASE}"
    rm "${RG_RELEASE}"
  fi
}

function install_login_shell() {
  local user zsh_path current_shell passwd_entry

  user=$(whoami)
  zsh_path=$(command -v zsh)
  if [ -z "$zsh_path" ] || ! [ -x "$zsh_path" ]; then
    echo "error: zsh is unavailable after package installation." >&2
    return 1
  fi

  if ! grep -Fxq "$zsh_path" /etc/shells; then
    printf '%s\n' "$zsh_path" | sudo tee -a /etc/shells >/dev/null
  fi

  if [ "$PLATFORM" == osx ]; then
    current_shell=$(dscl . -read "/Users/$user" UserShell)
    current_shell=${current_shell#UserShell: }
  else
    passwd_entry=$(getent passwd "$user") || {
      echo "error: unable to read the login shell for $user." >&2
      return 1
    }
    IFS=: read -r _ _ _ _ _ _ current_shell <<< "$passwd_entry"
  fi

  if [ "$current_shell" != "$zsh_path" ]; then
    sudo chsh -s "$zsh_path" "$user"
  fi
}

function install_common_settings() {
  echo "installing common settings..."
  stow -R -t "$HOME" stow


  # install the solarized dark theme for bat
  BAT_CONFIG_DIR="$(bat --config-dir)"
  (
    mkdir -p "${BAT_CONFIG_DIR}/themes"
    cd "${BAT_CONFIG_DIR}/themes"
    curl -L "https://raw.githubusercontent.com/braver/Solarized/87e01090cf5fb821a234265b3138426ae84900e7/Solarized%20(dark).tmTheme" \
      -o "Solarized (dark).tmTheme"
    bat cache --build
  )
}

function install_osx_settings() {
  echo "installing osx settings..."
  stow -R -t "$HOME" @mac
  defaults write com.apple.Dock autohide -bool TRUE
  defaults write com.apple.Finder AppleShowAllFiles -bool TRUE
  # disable natural scrolling
  defaults write -g com.apple.swipescrolldirection -bool FALSE
  # fastest key repeat rates
  defaults write -g InitialKeyRepeat -int 15 # 225 ms
  defaults write -g KeyRepeat -int 2 # 30 ms
  defaults write -g com.apple.trackpad.scaling 1
  # disable mouse acceleration
  defaults write .GlobalPreferences com.apple.mouse.scaling -1
  # enable dark mode
  osascript -e 'tell application "System Events" to tell appearance preferences to set dark mode to true'
}

function install_linux_settings() {
  echo "installing linux settings..."
  stow -R -t "$HOME" @linux
}

function install_vim_plug() {
  vim +PlugUpgrade +PlugUpdate +qa!
}

function install_zplug() {
  if [ -r "$HOME/.zplug/init.zsh" ]; then
    return 0
  fi

  if [ -e "$HOME/.zplug" ]; then
    echo "error: $HOME/.zplug exists but is incomplete; remove or repair it before rerunning setup." >&2
    return 1
  fi

  curl -fsSL --proto '=https' --proto-redir '=https' \
    https://raw.githubusercontent.com/zplug/installer/master/installer.zsh | zsh
}

function install_zplug_plugins() {
  if ! [ -r "$HOME/.zplugrc" ]; then
    echo "error: $HOME/.zplugrc is unavailable after stowing dotfiles." >&2
    return 1
  fi

  echo "installing missing Zsh plugins..."
  ZPLUG_LOADFILE="$HOME/.zplugrc" zsh -c '
    source "$HOME/.zplug/init.zsh"
    zplug check || zplug install
  '
}

function stow_dotfiles() {
  echo "stowing dotfiles from $SCRIPTDIR to $HOME..."
  stow -R -t "$HOME" git
  stow -R -t "$HOME" vim
  stow -R -t "$HOME" zsh
}

# run main installation
echo "dotfiles path: $SCRIPTDIR"

"install_${PACKAGE_MANAGER}_packages"

install_login_shell

# export gopath explicitly so go is installed in the proper location since
# .zshrc isn't sourced until after setup is complete
export GOPATH="$HOME/gocode"

install_common_settings
"install_${PLATFORM}_settings"
stow_dotfiles
install_zplug
install_zplug_plugins
install_vim_plug

echo "done!"
