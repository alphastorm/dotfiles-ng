**config files for `stow`<<https://www.gnu.org/software/stow/>>, the symlink farm manager**

    ├── stow
    │   └── .stow-global-ignore      Ignore files based on F-dotfiles filenaming scheme

### customization

[setup.sh](../setup.sh) passes `-t "$HOME"` to Stow. The [`.stow-global-ignore`](.stow-global-ignore) file is stowed into `~` and replaces Stow's built-in ignore list. Platform-specific packages live in [@mac/](../@mac/) and [@linux/](../@linux/).

The private repository's `omp-managed-skills` and `omp-plugins` branches are the source of truth for `~/.omp/agent/managed-skills` and `~/.omp/plugins`, which stay real Git worktrees rather than Stow links. The same commit/rebase/push convergence runs in `setup.sh` (also called by `update.sh`), at interactive OMP session start, and every 15 minutes during those sessions. Conflicts stop for manual resolution; cached Git resolutions are never applied.
