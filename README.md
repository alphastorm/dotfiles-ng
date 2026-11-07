# dotfiles-ng

Personal dotfiles and [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi) configuration, managed with [GNU Stow](https://www.gnu.org/software/stow/). Inspired by [Kraymer/F-dotfiles](https://github.com/Kraymer/F-dotfiles) (package layout) and [davehughes/dotfiles](https://github.com/davehughes/dotfiles) (setup and update scripts).

## Install

```bash
git clone https://github.com/alphastorm/dotfiles-ng.git ~/.dotfiles
~/.dotfiles/setup.sh
```

Run `./setup.sh --check` to report the public/private Git roots, commits, and branches checked, simulate package restows, and check key directories and LFS requirements without modifying `$HOME` or the checkouts. It reports a missing private checkout as skipped, rejects an existing non-root path, and fails if `stow` is not on `PATH`. Run `./update.sh` to fast-forward the checkouts and re-run `setup.sh`.

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
bun test ./tests ./omp/.omp/agent/skills/critical-review/lrhe/critical-review-policy.test.ts
./omp/.omp/agent/skills/critical-review/lrhe/review_checks.py quick
./omp/.omp/agent/skills/critical-review/lrhe/review_checks.py full
./benchmark.sh
bun scripts/lsp-smoke.ts <repo> definition:<file>:<line>:<symbol> diagnostics:<file>
```

`./setup.sh --check` requires Git and Stow on `PATH`; it installs nothing, changes no Git configuration, and neither restows nor synchronizes runtime worktrees. Both normal clones and linked worktrees are supported, including a private candidate selected with `DOTFILES_PRIVATE_DIR=/path/to/candidate ./setup.sh --check`. The final success line counts the package restows actually simulated; unavailable Stow is a failed validation, not a successful check. `tests/setup-check.test.ts` exercises isolated private fixtures and checks that validation leaves HOME and runtime worktrees unchanged.

`review_checks.py quick|full` requires `omp/.omp/agent/skills/critical-review/lrhe/.venv`, which `setup.sh` builds with `uv`. `./benchmark.sh` checks warmed shell startup median (`--runs 20 --budget-ms 500` by default).

`scripts/lsp-smoke.ts` runs OMP's own `lsp` tool in `<repo>` without a model, as a session started there would: the same `lsp.json` merge, server selection and executable resolution. It prints the host, the OMP version, the servers configured for that directory, and for each case the server that answered with its executable and version, then the result. A definition or hover that finds nothing, or a file no server answers for, exits 1; `diagnostics:<file>:<text>` also requires `<text>` in the answer. It drives this checkout's `@oh-my-pi/pi-coding-agent` and refuses to run when that pin differs from `omp --version`.
