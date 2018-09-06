#!/bin/bash

SCRIPTDIR=$(dirname "$0")
cd "$SCRIPTDIR" || exit

# detect platform-dependent options
OS=$(uname -a | cut -d" " -f 1)
echo "os: $OS"

if [ "$OS" == "Darwin" ]; then
  PLATFORM=osx
  if [ "$(command -v brew)" ]; then
    PACKAGE_MANAGER=brew
  else
    echo 'no package manager found.  install aptitude or yum to continue.' && exit 1
  fi
elif [ "$OS" == "Linux" ]; then
  PLATFORM=linux
  if [ "$(command -v apt-get)" ]; then
    PACKAGE_MANAGER=apt
  elif [ "$(command -v yum)" ]; then
    PACKAGE_MANAGER=yum
  else
    echo 'no package manager found.  install aptitude or yum to continue.' && exit 1
  fi
else
  echo "unrecognized os: $OS" && exit 1
fi
echo "platform: $PLATFORM"
echo "package manager: $PACKAGE_MANAGER"

function install_brew_packages() {
  echo "installing homebrew packages..."
  brew install \
    stow \
    cmake \
    coreutils \
    csshx \
    go \
    node \
    gpg \
    httpie \
    jq \
    tree \
    macvim --with-override-system-vim \
    pinentry \
    pinentry-mac \
    shellcheck \
    wget \
    zsh

  brew cask install keybase

  # homebrew-core's version is compiled with rust stable and no SIMD. install
  # a binary compiled with rust nightly (including SIMD and all optimizations)
  # by utilizing a custom tap:
  brew tap burntsushi/ripgrep https://github.com/BurntSushi/ripgrep.git
  brew install burntsushi/ripgrep/ripgrep-bin
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

  if ! [ -x "$(command -v rg)" ]; then
    RG_REPO="https://github.com/BurntSushi/ripgrep/releases/download"
    RG_LATEST=$(curl -sSL "https://api.github.com/repos/BurntSushi/ripgrep/releases/latest" | jq --raw-output .tag_name)
    RG_RELEASE="ripgrep_${RG_LATEST}_amd64.deb"

    curl -LO "${RG_REPO}/${RG_LATEST}/${RG_RELEASE}"
    sudo dpkg -i "${RG_RELEASE}" && rm "${RG_RELEASE}"
  fi
}

function install_yum_packages() {
  echo "installing yum packages..."
  # TODO: update
  # sudo yum -y install
}

function install_common_settings() {
  echo "installing common settings..."
  stow -R -t ~ stow
  sudo pip install virtualenv
}

function install_osx_settings() {
  echo "installing osx settings..."
  stow -R -t ~ @mac
  defaults write com.apple.Finder AppleShowAllFiles -bool TRUE
  defaults write NSGlobalDomain com.apple.swipescrolldirection -bool FALSE

  mkdir -p ~/Library/Fonts &&
    cd ~/Library/Fonts &&
    { curl -LO "https://raw.githubusercontent.com/powerline/fonts/master/Inconsolata-g/Inconsolata-g%20for%20Powerline.otf"; cd - || return; }
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

install_common_settings
install_${PLATFORM}_settings
stow_dotfiles
install_zplug
install_vim_plug

echo "done!"
