#!/usr/bin/env bash
# BUILDER role: check out the Bluepad32 source the observer firmware builds against, at the pinned ref, with its BTstack
# submodule and Bluepad32's own BTstack patches (external/patches) applied -- what builder/build-observers.sh needs.
#
#   builder/fetch-bluepad32.sh <dest>          # [observer].bluepad32_repo @ [observer].bluepad32_ref
#
# $HIL_BLUEPAD32_REPO / $HIL_BLUEPAD32_REF override the config. A <dest> already at the ref with the patches applied is
# left alone, so repeat runs (and CI caches) cost nothing. A blobless clone keeps the history and tags, so
# `git describe` (the bundle's lib_describe) reads the same as a full checkout.
set -euo pipefail
cd "$(dirname "$0")/.."
cfg() { python3 host/hil/config.py "$1"; }

dest=${1:?usage: builder/fetch-bluepad32.sh <dest>}
url=${HIL_BLUEPAD32_REPO:-$(cfg observer.bluepad32_repo)}
ref=${HIL_BLUEPAD32_REF:-$(cfg observer.bluepad32_ref)}
[[ -n $url && -n $ref ]] || { echo "set [observer].bluepad32_repo and bluepad32_ref" >&2; exit 2; }
marker="$dest/.git/hil-btstack-patched"

if [[ -f $marker && $(git -C "$dest" rev-parse HEAD) == $(git -C "$dest" rev-parse "$ref^{commit}" 2>/dev/null) ]]; then
  echo "== bluepad32 $dest already at $ref, patched"
  exit 0
fi

echo "== bluepad32 $url @ $ref -> $dest"
rm -rf "$dest"
git clone -q --filter=blob:none --no-checkout "$url" "$dest"
git -C "$dest" checkout -q --detach "$ref"
git -C "$dest" submodule update -q --init --recursive
shopt -s nullglob
patches=("$dest"/external/patches/*.patch)
for p in "${patches[@]}"; do
  echo "== patch btstack: $(basename "$p")"
  git -C "$dest/external/btstack" apply "$p"
done
touch "$marker"
echo "== bluepad32 $(git -C "$dest" describe --tags --always --dirty)"
