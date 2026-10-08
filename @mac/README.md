**mac specific setup**

    ├── @mac
    │   ├── .config
    │   │   └── .gitconfig.local
    │   ├── .gnupg
    │   │   ├── gpg-agent.conf
    │   │   └── gpg.conf
    │   ├── .iterm2
    │   │   └── com.googlecode.iterm2.plist
    │   ├── Library
    │   │   └── Application Support
    │   │       └── lspmux
    │   │           └── config.toml
    │   ├── .stowrc
    │   └── .zsh
    │       ├── mac-vars.zshenv
    │       └── work.sec.zsh.example

### customization

set iterm2 to load/save preferences from a custom folder: `~/.iterm2`.

`setup.sh` installs rustup with its own installer (leaving shell profiles
alone: `zsh/.zshenv` puts `~/.cargo/bin` on PATH), then lspmux, and links
`launchd/org.codeberg.p2502.lspmux.plist` into `~/Library/LaunchAgents` only
after the binary exists. OMP routes rust-analyzer through the running server.
