"""Load hil_config.toml with an optional gitignored hil_config.local.toml
deep-merged over it. Usable as a module (from pytest) or a CLI (from shell):

    python3 host/hil/config.py rig.pio
    python3 host/hil/config.py board.esp32dev.port
"""

import pathlib
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

REPO = pathlib.Path(__file__).resolve().parents[2]


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(repo_root=REPO):
    root = pathlib.Path(repo_root)
    cfg = tomllib.loads((root / "hil_config.toml").read_text())
    local = root / "hil_config.local.toml"
    if local.exists():
        _merge(cfg, tomllib.loads(local.read_text()))
    return cfg


def get(dotted, default=""):
    cur = load()
    for k in dotted.split("."):
        cur = cur.get(k) if isinstance(cur, dict) else None
        if cur is None:
            return default
    return cur


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--boards":
        print(" ".join(load().get("board", {})))
    else:
        val = get(sys.argv[1])
        print("" if isinstance(val, (dict, list)) else val)
