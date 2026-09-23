#!/bin/bash
set -euo pipefail

SCRIPTDIR=$(dirname "$0")
cd "$SCRIPTDIR" || exit

# `./setup.sh --check` is the no-effect validation path: it runs the preflight
# below against the machine as it stands -- nothing installed, nothing restowed
# -- and exits.
CHECK_ONLY=
if [ "${1:-}" = --check ]; then
  CHECK_ONLY=1
fi
PRIVATE_DIR=${DOTFILES_PRIVATE_DIR:-"$HOME/.dotfiles-private"}

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

  # A check installs nothing, Homebrew included.
  if ! load_brew && [ -z "$CHECK_ONLY" ]; then
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
  HOMEBREW_NO_AUTO_UPDATE=1 brew install \
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

  HOMEBREW_NO_AUTO_UPDATE=1 brew install --cask \
    font-meslo-lg-nerd-font \
    keybase
}

function install_apt_packages() {
  echo "installing APT packages..."
  sudo apt-get update

  sudo apt-get install -y \
    bat \
    bsdextrautils \
    build-essential \
    cmake \
    curl \
    gawk \
    git \
    gnupg \
    jq \
    lsd \
    nodejs \
    npm \
    openssh-client \
    python3-dev \
    python3-pip \
    ripgrep \
    screen \
    shellcheck \
    stow \
    tree \
    unzip \
    vim \
    zsh

  # Debian installs bat as batcat to avoid a package-name collision.
  if ! command -v bat >/dev/null 2>&1 &&
     command -v batcat >/dev/null 2>&1; then
    mkdir -p "$HOME/.local/bin"
    ln -sfn "$(command -v batcat)" "$HOME/.local/bin/bat"
    export PATH="$HOME/.local/bin:$PATH"
  fi

  if ! command -v bat >/dev/null 2>&1; then
    echo "error: the bat package did not provide bat or batcat." >&2
    return 1
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


  # install the solarized dark theme for bat once
  local bat_config_dir theme_file
  bat_config_dir=$(bat --config-dir)
  theme_file="${bat_config_dir}/themes/Solarized (dark).tmTheme"
  if ! [ -r "$theme_file" ]; then
    (
      mkdir -p "${bat_config_dir}/themes"
      cd "${bat_config_dir}/themes"
      curl -fL --proto '=https' --proto-redir '=https' \
        "https://raw.githubusercontent.com/braver/Solarized/87e01090cf5fb821a234265b3138426ae84900e7/Solarized%20(dark).tmTheme" \
        -o "Solarized (dark).tmTheme"
      bat cache --build
    )
  fi
}

function install_osx_settings() {
  echo "installing osx settings..."
  stow -D -t "$HOME" @mac
  mkdir -p "$HOME/.zsh"
  stow -S -t "$HOME" @mac
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

function _verify_sha256() {
  local expected file
  expected=$1
  file=$2

  if command -v shasum >/dev/null 2>&1; then
    printf '%s  %s\n' "$expected" "$file" |
      shasum -a 256 --check >/dev/null 2>&1
  elif command -v sha256sum >/dev/null 2>&1; then
    printf '%s  %s\n' "$expected" "$file" |
      sha256sum -c >/dev/null 2>&1
  else
    echo "error: sha256sum or shasum is required to verify vim-plug." >&2
    return 1
  fi
}

function _migrate_vim_plugin_remote() {
  local current_url directory expected_url
  directory=$1
  expected_url=$2

  if ! [ -d "$directory/.git" ]; then
    return 0
  fi
  if ! current_url=$(git -C "$directory" remote get-url origin); then
    echo "error: $directory has no origin remote." >&2
    return 1
  fi
  if [ "$current_url" != "$expected_url" ]; then
    git -C "$directory" remote set-url origin "$expected_url"
  fi
}


function install_vim_plug() {
  local commit destination expected_sha256 url
  commit=88e31471818e9a29a8a20a0ee61360cfd7bdc1cd
  expected_sha256=7e2b20cd909da9c456498684c98f03c63829170f01e34595dd8e1818a217d37c
  destination="$HOME/.vim/autoload/plug.vim"
  url="https://raw.githubusercontent.com/junegunn/vim-plug/$commit/plug.vim"

  if ! _verify_sha256 "$expected_sha256" "$destination"; then
    echo "installing pinned vim-plug..."
    mkdir -p "$HOME/.vim/autoload"
    (
      local cleanup_command temporary_file
      temporary_file=$(mktemp "$destination.tmp.XXXXXX")
      printf -v cleanup_command 'rm -f %q' "$temporary_file"
      # Capture the path before local scope exits.
      # shellcheck disable=SC2064
      trap "$cleanup_command" EXIT
      curl -fsSL --proto '=https' --proto-redir '=https' \
        "$url" -o "$temporary_file"
      if ! _verify_sha256 "$expected_sha256" "$temporary_file"; then
        echo "error: vim-plug checksum verification failed." >&2
        exit 1
      fi
      chmod 0644 "$temporary_file"
      mv -f "$temporary_file" "$destination"
      trap - EXIT
    )
  fi

  _migrate_vim_plugin_remote "$HOME/.vim/plugged/ale" \
    https://github.com/dense-analysis/ale.git
  _migrate_vim_plugin_remote "$HOME/.vim/plugged/nerdcommenter" \
    https://github.com/preservim/nerdcommenter.git
  _migrate_vim_plugin_remote "$HOME/.vim/plugged/nerdtree" \
    https://github.com/preservim/nerdtree.git
  _migrate_vim_plugin_remote "$HOME/.vim/plugged/indentline" \
    https://github.com/preservim/vim-indentline.git
  _migrate_vim_plugin_remote "$HOME/.vim/plugged/youcompleteme" \
    https://github.com/ycm-core/YouCompleteMe.git

  echo "installing missing Vim plugins..."
  vim '+PlugInstall --sync' +qa!
}

function install_zplug() {
  local cleanup_command commit expected_sha256 installer url
  if [ -r "$HOME/.zplug/init.zsh" ]; then
    return 0
  fi

  if [ -e "$HOME/.zplug" ]; then
    echo "error: $HOME/.zplug exists but is incomplete; remove or repair it before rerunning setup." >&2
    return 1
  fi

  commit=408816a046d034f168efdf452acf2a4b544ad1bb
  expected_sha256=5217cccd6ff5e7d085aa266e69ae9ca61ae54f582ca0f0d8a6069c63ecbf3592
  url="https://raw.githubusercontent.com/zplug/installer/$commit/installer.zsh"
  (
    installer=$(mktemp "${TMPDIR:-/tmp}/zplug-installer.XXXXXX")
    printf -v cleanup_command 'rm -f %q' "$installer"
    # Capture the path before local scope exits.
    # shellcheck disable=SC2064
    trap "$cleanup_command" EXIT

    curl -fsSL --proto '=https' --proto-redir '=https' \
      "$url" -o "$installer"
    if ! _verify_sha256 "$expected_sha256" "$installer"; then
      echo "error: zplug installer checksum verification failed." >&2
      exit 1
    fi
    zsh "$installer"
    trap - EXIT
    rm -f "$installer"
  )
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

  stow -D -t "$HOME" omp
  # Pre-create every directory that something OTHER than a single stow package
  # writes into. Stow folds a directory into one symlink when exactly one package
  # owns it, and a folded directory then belongs to that package's repository --
  # so anything else writing there writes into the repository.
  #
  # Two kinds qualify:
  #   - shared between packages: `critical-review` takes the skill and the LRHE
  #     harness from public, the corpus and answer key from private.
  #   - written by the OMP runtime: `profiles/audit/agent` holds agent.db,
  #     history.db, models.db and their WAL files. Fold it and OMP writes live
  #     databases into .dotfiles-private on every run.
  #
  # `agent/agents` is deliberately NOT here. Nothing but agent definitions lives
  # in it and only the private package owns them, so letting it fold makes the
  # whole directory one symlink into the repository. That is what stops
  # `omp agents unpack --user` from re-breaking stow: an unpacked file lands in
  # the private checkout as a visible, revertible diff instead of as a foreign
  # real file that stow refuses to overwrite and aborts the whole package on.
  #
  # `agent/managed-skills` and `plugins` are in neither group: no stow layout can
  # own a directory whose writer refuses links or renames files over them. They
  # are runtime worktrees -- see ensure_runtime_worktree.
  mkdir -p \
    "$HOME/.omp" \
    "$HOME/.omp/agent" \
    "$HOME/.omp/agent/extensions" \
    "$HOME/.omp/agent/skills" \
    "$HOME/.omp/agent/skills/critical-review" \
    "$HOME/.omp/profiles/audit/agent"
  stow -S -t "$HOME" omp
}

# Directories an OMP runtime writer owns, one "<branch> <path>" line each. Each
# is a git worktree of the private repository on its own branch, never a stow
# target: stow can only offer links, and these writers refuse or replace them.
#   - `manage_skill` and `learn` refuse a symlinked managed-skills root, skill
#     directory, or SKILL.md, so the fold that once tracked the skills failed
#     every write.
#   - `omp plugin install` runs bun in ~/.omp/plugins, and bun saves bun.lock by
#     renaming a temp file over it. The stowed link became a real file, and the
#     next `stow -R omp-private` aborted the whole package on it.
# In a worktree the runtime writes real files, and every change it makes is
# still a visible, revertible, pushable diff: `git -C <path> status`.
function runtime_worktrees() {
  printf '%s %s\n' \
    omp-managed-skills "$HOME/.omp/agent/managed-skills" \
    omp-plugins "$HOME/.omp/plugins"
}

function is_runtime_worktree() {
  local branch=$1 target=$2
  [ "$(git -C "$target" rev-parse --show-toplevel 2>/dev/null)" = "$(cd "$target" 2>/dev/null && pwd -P)" ] &&
    [ "$(git -C "$target" branch --show-current 2>/dev/null)" = "$branch" ]
}

function ensure_runtime_worktree() {
  local branch=$1 target=$2

  # A symlink here is a stow fold from the old layout. `stow -R` leaves it
  # behind dangling, and a link holds no data.
  if [ -L "$target" ]; then
    rm "$target"
  fi
  if [ ! -e "$target" ]; then
    # A deleted worktree stays registered, and `worktree add` refuses a
    # registered path until the registration is pruned.
    git -C "$PRIVATE_DIR" worktree prune
    git -C "$PRIVATE_DIR" worktree add "$target" "$branch"
  fi
  if ! is_runtime_worktree "$branch" "$target"; then
    echo "error: $target must be a git worktree of $PRIVATE_DIR on branch $branch" >&2
    return 1
  fi
  if [ -n "$(git -C "$target" status --porcelain)" ]; then
    echo "note: uncommitted changes in $target" >&2
  fi
}

# Every package this script stows, one "<package> <stow directory>" line each,
# in the order the steps below restow them. preflight_stow simulates exactly
# this list, so a package added to those steps belongs here too.
function stow_packages() {
  local platform_package=@mac
  [ "$PLATFORM" = osx ] || platform_package=@linux
  printf '%s %s\n' stow "$PWD" "$platform_package" "$PWD" git "$PWD" vim "$PWD" zsh "$PWD" omp "$PWD"
  if [ -d "$PRIVATE_DIR/.git" ]; then
    printf '%s %s\n' omp-private "$PRIVATE_DIR"
    if [ "$OS" == "Darwin" ]; then
      printf '%s %s\n' zsh-private "$PRIVATE_DIR"
    fi
  fi
}

# Simulate every restow before anything changes. Stow aborts a whole package on
# its first conflict -- a real file where one of its links belongs, which is what
# a runtime saving by rename leaves behind -- and `set -e` then stopped this
# script at that package, with everything before it applied and everything after
# it skipped. Now it stops up front and names every conflict at once.
function preflight_stow() {
  local failed=0 package dir output branch target
  if ! command -v stow >/dev/null; then
    return 0 # first run: stow arrives with the packages, and nothing is stowed yet
  fi
  while read -r package dir; do
    [ -d "$dir/$package" ] || continue
    if ! output=$(stow --simulate --restow --dir "$dir" --target "$HOME" "$package" 2>&1); then
      printf 'conflict: %s\n%s\n' "$package" "$output" >&2
      failed=1
    fi
  done <<EOF
$(stow_packages)
EOF
  if [ -d "$PRIVATE_DIR/.git" ]; then
    while read -r branch target; do
      if [ -e "$target" ] && [ ! -L "$target" ] && ! is_runtime_worktree "$branch" "$target"; then
        echo "conflict: $target must be a git worktree of $PRIVATE_DIR on branch $branch" >&2
        failed=1
      fi
    done <<EOF
$(runtime_worktrees)
EOF
  fi
  if [ "$failed" -ne 0 ]; then
    echo "error: nothing was changed; resolve the conflicts above, then rerun" >&2
    return 1
  fi
}

function stow_private_dotfiles() {
  local private_dir=$PRIVATE_DIR branch target

  if [ -e "$private_dir" ] || [ -L "$private_dir" ]; then
    if ! [ -d "$private_dir/.git" ]; then
      echo "error: private dotfiles path is not a Git checkout: $private_dir" >&2
      return 1
    fi
  elif ! git clone git@github.com:alphastorm/dotfiles-private.git "$private_dir"; then
    echo "warning: private dotfiles unavailable; continuing with public configuration." >&2
    return 0
  fi

  # Before stow, not after: the global ignore keeps stow off these paths either
  # way, and an unrelated package conflict must not strand a stale fold.
  while read -r branch target; do
    ensure_runtime_worktree "$branch" "$target" </dev/null
  done <<EOF
$(runtime_worktrees)
EOF

  # -R, not -S. A plain -S leaves stale links behind when a file moves between the
  # public and private packages: the old package's link survives, the new package
  # refuses to overwrite something it does not own, and stow then aborts the whole
  # operation. That is exactly how five agent definitions ended up unstowable, three
  # of them dangling and silently not loading. The public package above already
  # unstows first; do the same here so a rerun repairs rather than jams.
  stow -R -d "$private_dir" -t "$HOME" omp-private
  # Code Mode rejects optional extension targets that group or other users may
  # rewrite. Normalize the source package after stow because the live entries are
  # symlinks into this checkout and therefore inherit its modes.
  if [ -d "$private_dir/omp-private/.omp/agent/extensions" ]; then
    chmod -R go-w "$private_dir/omp-private/.omp/agent/extensions"
  fi
  if [ "$OS" == "Darwin" ]; then
    stow -R -d "$private_dir" -t "$HOME" zsh-private
  fi
}

# run main installation
echo "dotfiles path: $SCRIPTDIR"

# Before anything changes: an install that cannot be restowed stops here with
# every conflict listed, instead of partway through.
preflight_stow
if [ -n "$CHECK_ONLY" ]; then
  echo "check passed: every package would restow without a conflict"
  exit 0
fi

"install_${PACKAGE_MANAGER}_packages"

install_login_shell


install_common_settings
"install_${PLATFORM}_settings"
stow_dotfiles
stow_private_dotfiles
install_zplug
install_zplug_plugins
install_vim_plug

echo "done!"
