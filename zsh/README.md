**config files for `zsh`<<http://www.zsh.org/>>, a shell designed for interactive use**

    └── zsh
        ├── .zlogin
        ├── .zprofile
        ├── .zshenv
        └── .zshrc

uses:

* [zplug](https://github.com/zplug/zplug) (plugin manager)
* [prezto](https://github.com/sorin-ionescu/prezto) (configuration framework)

### customization

others packages define environment variables or functions by writing shell files into `~/.zsh`.

`~/.zshenv` sources `~/.zsh/**/*.zshenv(N)` at zsh startup; `~/.zshrc` sources `~/.zsh/**/*.zsh(N)` for interactive shells. Both globs recurse through `~/.zsh`, and the `(N)` qualifier allows no matching files.
