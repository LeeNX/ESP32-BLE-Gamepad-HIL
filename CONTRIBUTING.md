# Contributing

## Formatting & linting

All formatting and linting runs through [pre-commit](https://pre-commit.com).
The CI job `.github/workflows/lint.yml` runs the exact same
`.pre-commit-config.yaml`, so a green `pre-commit run --all-files` locally means
the lint gate passes.

### One-time setup

```sh
pipx install pre-commit        # or: brew install pre-commit / uv tool install pre-commit
pre-commit install             # runs the hooks on every `git commit`
```

`actionlint` uses the system binary (the other tools are vendored by
pre-commit). Install it once:

```sh
brew install actionlint        # macOS
# or, anywhere:
bash <(curl -fsSL https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash)
```

`shellcheck` is bundled via `shellcheck-py`, but `actionlint` also shells out to
a system `shellcheck` when it finds one (`brew install shellcheck`) to lint
`run:` scripts inside workflows.

### Everyday use

```sh
pre-commit run --all-files              # everything
pre-commit run ruff-format --all-files  # one hook
git commit                              # staged files only, auto-runs
SKIP=actionlint git commit              # skip a hook for one commit
```

### What runs

| Area | Tool | Config | Enforced |
| --- | --- | --- | --- |
| Python lint | `ruff check` | `pyproject.toml` | yes (autofixes) |
| Python format | `ruff format` | `pyproject.toml` | yes |
| Shell lint | `shellcheck -x` | inline `# shellcheck` directives | yes |
| Shell format | — | — | no — scripts are hand-formatted; shfmt can't preserve the compact style |
| Markdown | `markdownlint-cli2` | `.markdownlint-cli2.yaml` | yes |
| TOML | `taplo lint` | `.taplo.toml` | syntax/schema only — formatting is not enforced (it collapses the aligned comments in `hil_config.toml`) |
| Workflows | `actionlint` | — | yes |
| Whitespace / EOF / line endings | `pre-commit-hooks` | `.editorconfig` | yes |

### Running a tool directly

```sh
uvx ruff check .            # or: pipx run ruff check .
uvx ruff format .
shellcheck -x $(git ls-files '*.sh')
npx markdownlint-cli2
uvx --from taplo taplo lint
actionlint
```

### The ruff rule set

`select` in `pyproject.toml` is deliberately small for now: pycodestyle
errors, pyflakes, pyupgrade, isort. `line-length = 100`; the formatter owns
line length, so `E501` is not selected. Bugbear (`B`), flake8-simplify (`SIM`)
and flake8-comprehensions (`C4`) are the obvious next additions once the gate
is established — each needs a handful of existing call sites cleaned up first.

## Tests

The pytest suite (`host/tests/`) needs the physical rig (ESP32 + BLE adapter on
Linux with BlueZ). See [README.md](README.md) for running it locally or via the
`hil.yml` workflow. The lint gate above has no hardware dependency and should
pass on any machine.
