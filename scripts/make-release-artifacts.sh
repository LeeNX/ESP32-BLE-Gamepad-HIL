#!/usr/bin/env bash
# Assemble the release payload from an already-built bundles/ tree into dist/:
#
#   dist/esp32-ble-gamepad-hil-firmware-<tag>.tar.gz   firmware-bundles/ + golden/ + index.json + REPRODUCE.md
#   dist/esp32-ble-gamepad-hil-suite-<tag>.tar.gz      a standalone copy of the pytest suite
#   dist/SHA256SUMS
#
# Run by .github/workflows/release.yml after builder/build.sh. Standalone-safe so
# you can dry-run a release payload locally:  ./run.sh --profiles default ;
# scripts/make-release-artifacts.sh vX.Y.Z-test
#
# Provenance (LIB_SHA / LIB_DESCRIBE / LIB_REPO) is read from the environment if
# set (the workflow exports it), else derived from rig.lib_dir.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$(pwd)

TAG=${1:?usage: make-release-artifacts.sh <tag>}
if ! compgen -G "$REPO/bundles/*/manifest.json" >/dev/null; then
  echo "error: no bundles/ -- run builder/build.sh first" >&2; exit 1
fi

LIB_DIR=$(python3 host/hil/config.py rig.lib_dir 2>/dev/null || true)
: "${LIB_REPO:=$( [[ -d $LIB_DIR/.git ]] && git -C "$LIB_DIR" remote get-url origin 2>/dev/null || echo unknown )}"
: "${LIB_SHA:=$( [[ -d $LIB_DIR/.git ]] && git -C "$LIB_DIR" rev-parse HEAD 2>/dev/null || echo unknown )}"
: "${LIB_DESCRIBE:=$( [[ -d $LIB_DIR/.git ]] && git -C "$LIB_DIR" describe --tags --always 2>/dev/null || echo unknown )}"
RIG_SHA=$(git -C "$REPO" rev-parse HEAD)

stage="$REPO/dist/staging"
rm -rf "$REPO/dist"
mkdir -p "$stage"

cp -r "$REPO/bundles"          "$stage/firmware-bundles"
cp -r "$REPO/firmware/golden"  "$stage/golden"
cp    "$REPO/REPRODUCE.md"     "$stage/REPRODUCE.md"

mkdir -p "$stage/suite"
cp -r "$REPO/host" "$REPO/tester" "$stage/suite/"
cp    "$REPO/conftest.py" "$REPO/pytest.ini" "$REPO/hil_config.toml" "$stage/suite/"
find "$stage/suite" -name __pycache__ -type d -prune -exec rm -rf {} +

python3 - "$TAG" "$RIG_SHA" "$LIB_REPO" "$LIB_SHA" "$LIB_DESCRIBE" > "$stage/index.json" <<'PY'
import datetime as dt, json, pathlib, sys
tag, rig_sha, lib_repo, lib_sha, lib_describe = sys.argv[1:6]
bundles = []
# matrix/ holds the bundles test-all.sh doesn't run (sinput); they ship too.
root = pathlib.Path("bundles")
for m in sorted([*root.glob("*/manifest.json"), *root.glob("matrix/*/manifest.json")]):
    d = json.loads(m.read_text())
    bundles.append({k: d[k] for k in ("board", "chip", "profile", "pio_env",
                                      "lib_sha", "lib_describe")}
                   | {"dir": str(m.parent.relative_to(root))})
# Observer bundles (matrix/<board>-bp32obs-*) are Bluepad32 builds: their lib_sha is the Bluepad32 commit, not the
# ESP32-BLE-Gamepad library's, so it gets its own provenance entry.
obs = [b for b in bundles if b["profile"] == "bp32obs"]
bluepad32 = {"sha": obs[0]["lib_sha"], "describe": obs[0]["lib_describe"]} if obs else None
print(json.dumps({
    "rig_version": tag.lstrip("v"),
    "rig_sha": rig_sha,
    "library": {"repo": lib_repo, "sha": lib_sha, "describe": lib_describe},
    **({"bluepad32": bluepad32} if bluepad32 else {}),
    "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    "boards": sorted({b["board"] for b in bundles}),
    "profiles": sorted({b["profile"] for b in bundles}),
    "bundles": bundles,
}, indent=2))
PY

fw="esp32-ble-gamepad-hil-firmware-${TAG}.tar.gz"
suite="esp32-ble-gamepad-hil-suite-${TAG}.tar.gz"
tar -C "$stage" -czf "$REPO/dist/$fw"    firmware-bundles golden index.json REPRODUCE.md
tar -C "$stage" -czf "$REPO/dist/$suite" suite index.json REPRODUCE.md
( cd "$REPO/dist" && sha256sum "$fw" "$suite" > SHA256SUMS )
rm -rf "$stage"

echo "== dist/"
ls -l "$REPO/dist"
