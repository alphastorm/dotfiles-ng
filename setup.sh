#!/bin/bash

SCRIPTDIR=$(dirname "$0")
cd "$SCRIPTDIR" || exit

# detect platform-dependent options
OS=$(uname -a | cut -d" " -f 1)
echo "os: $OS"

if [ "$OS" == "Darwin" ]; then
  PLATFORM=osx
  PACKAGE_MANAGER=brew
  if ! [ -x "$(command -v brew)" ]; then
    /usr/bin/ruby -e "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install)"
  fi
elif [ "$OS" == "Linux" ]; then
  PLATFORM=linux
  if [ "$(command -v apt-get)" ]; then
    PACKAGE_MANAGER=apt
  else
    echo 'no package manager found.  install aptitude to continue.' && exit 1
  fi
else
  echo "unrecognized os: $OS" && exit 1
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
    ripgrep \
    semgrep \
    shellcheck \
    stow \
    tree \
    wget \
    zsh
  brew install macvim

  brew cask install keybase
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
    sudo dpkg -i "${BAT_RELEASE}" && rm "${BAT_RELEASE}"
  fi

  # install rg if not present
  if ! [ -x "$(command -v rg)" ]; then
    RG_REPO="https://github.com/BurntSushi/ripgrep/releases/download"
    RG_LATEST=$(curl -sSL "https://api.github.com/repos/BurntSushi/ripgrep/releases/latest" | jq --raw-output .tag_name)
    RG_RELEASE="ripgrep_${RG_LATEST}_amd64.deb"

    curl -LO "${RG_REPO}/${RG_LATEST}/${RG_RELEASE}"
    sudo dpkg -i "${RG_RELEASE}" && rm "${RG_RELEASE}"
  fi
}

function install_common_settings() {
  echo "installing common settings..."
  stow -R -t ~ stow
  sudo easy_install pip
  sudo pip install virtualenv

  # install the solarized dark theme for bat
  BAT_CONFIG_DIR="$(bat --config-dir)"
  mkdir -p "${BAT_CONFIG_DIR}/themes" &&
    cd "${BAT_CONFIG_DIR}/themes" &&
    { curl -L "https://raw.githubusercontent.com/braver/Solarized/87e01090cf5fb821a234265b3138426ae84900e7/Solarized%20(dark).tmTheme" \
      -o "Solarized (dark).tmTheme"; cd - || return; }
  bat cache --build
}

function install_osx_settings() {
  echo "installing osx settings..."
  stow -R -t ~ @mac
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

  mkdir -p ~/Library/Fonts &&
    cd ~/Library/Fonts && {
      curl -fsLO "https://github.com/romkatv/powerlevel10k-media/raw/master/MesloLGS%20NF%20Regular.ttf"
      curl -fsLO "https://github.com/romkatv/powerlevel10k-media/raw/master/MesloLGS%20NF%20Bold.ttf"
      curl -fsLO "https://github.com/romkatv/powerlevel10k-media/raw/master/MesloLGS%20NF%20Italic.ttf"
      curl -fsLO "https://github.com/romkatv/powerlevel10k-media/raw/master/MesloLGS%20NF%20Bold%20Italic.ttf"
      cd - > /dev/null || return;
  }
}

function install_linux_settings() {
  echo "installing linux settings..."
  stow -R -t ~ @linux
}

function install_vim_plug() {
  vim +PlugUpgrade +PlugUpdate +qa!
}

function install_zplug() {
  curl -sL --proto-redir -all,https https://raw.githubusercontent.com/zplug/installer/master/installer.zsh | zsh
}

function stow_dotfiles() {
  echo "stowing dotfiles from $SCRIPTDIR to $HOME..."
  stow -R git
  stow -R vim
  stow -R zsh
}

# run main installation
echo "dotfiles path: $SCRIPTDIR"

install_${PACKAGE_MANAGER}_packages

# set shell
sudo chsh -s "$(command -v zsh)" "$(whoami)"

# export gopath explicitly so go is installed in the proper location since
# .zshrc isn't sourced until after setup is complete
export GOPATH="$HOME/gocode"

install_common_settings
install_${PLATFORM}_settings
stow_dotfiles
install_zplug
install_vim_plug

echo "done!"
