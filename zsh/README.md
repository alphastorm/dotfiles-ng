**config files for `zsh`<<http://www.zsh.org/>>, a shell designed for interactive use**

    └── zsh
        ├── .promptline.sh
        ├── .zlogin
        ├── .zprofile
        ├── .zshenv
        └── .zshrc

uses:

* [zplug](https://github.com/zplug/zplug) (plugin manager)
* [prezto](https://github.com/sorin-ionescu/prezto) (configuration framework)

### customization

others packages define environment variables or functions by writing shell files into `~/.zsh`.

`~/.zsshenv` sources all **.zshenv* files present in `~/.zsh` subfolders at zsh startup, and `~/.zshrc` does the same with **.zsh* files.
