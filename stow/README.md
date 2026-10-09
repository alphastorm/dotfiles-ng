**config files for `stow`<<https://www.gnu.org/software/stow/>>, the symlink farm manager**

    ├── stow
    │   └── .stow-global-ignore      Ignore files based on F-dotfiles filenaming scheme

### customization

there are platform specific `.stowrc` files in [@linux/.stowrc](https://github.com/alphastorm/dotfiles-ng/blob/master/%40linux/.stowrc) and [@mac/.stowrc](https://github.com/alphastorm/dotfiles-ng/blob/master/%40mac/.stowrc) (for `$HOME`).

The private repository's `omp-managed-skills` and `omp-plugins` branches are the source of truth for `~/.omp/agent/managed-skills` and `~/.omp/plugins`, which stay real Git worktrees rather than Stow links. The same commit/rebase/push convergence runs in `setup.sh` (also called by `update.sh`), at interactive OMP session start, and every 15 minutes during those sessions. Conflicts stop for manual resolution; cached Git resolutions are never applied.
