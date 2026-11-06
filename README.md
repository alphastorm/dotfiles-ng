# dotfiles-ng

Personal dotfiles and [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi) configuration, managed with [GNU Stow](https://www.gnu.org/software/stow/). Inspired by [Kraymer/F-dotfiles](https://github.com/Kraymer/F-dotfiles) (package layout) and [davehughes/dotfiles](https://github.com/davehughes/dotfiles) (setup and update scripts).

## Install

```bash
git clone https://github.com/alphastorm/dotfiles-ng.git ~/.dotfiles
~/.dotfiles/setup.sh
```

Run `./setup.sh --check` to simulate every Stow restow (when `stow` is installed) and check key directories without modifying `$HOME`. Run `./update.sh` to fast-forward the checkout and re-run `setup.sh`.

A separate private companion repository supplies host-specific configuration through the same setup script (`~/.dotfiles-private` by default, or `DOTFILES_PRIVATE_DIR`).

## Layout

Each Stow package is a top-level directory stowed into `$HOME`:

- `stow/` — `.stow-global-ignore` (stowed to `~/.stow-global-ignore` first so Stow ignores `README.*`, `node_modules`, and `.venv`).
- `@mac/` and `@linux/` — platform-specific Stow packages selected by `setup.sh`.
- `git/`, `vim/`, `zsh/` — Git, Vim, and Zsh configuration (`README.md` in each package).
- `omp/` — OMP agent config, extensions (`omp/.omp/agent/extensions/`), and skills (`omp/.omp/agent/skills/critical-review/`).

`scripts/`, `tests/`, and `launchd/` hold helper scripts, TypeScript tests, and launchd templates (not stowed).

## Checks

```bash
./setup.sh --check
bun install --frozen-lockfile && bun run typecheck
bun test ./tests/stow-health-extension.test.ts ./omp/.omp/agent/skills/critical-review/lrhe/critical-review-policy.test.ts
./omp/.omp/agent/skills/critical-review/lrhe/review_checks.py quick
./omp/.omp/agent/skills/critical-review/lrhe/review_checks.py full
./benchmark.sh
```

`review_checks.py quick|full` requires `omp/.omp/agent/skills/critical-review/lrhe/.venv`, which `setup.sh` builds with `uv`. `./benchmark.sh` checks warmed shell startup median (`--runs 20 --budget-ms 500` by default).
