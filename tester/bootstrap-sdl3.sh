#!/usr/bin/env bash
# TESTER role, unprivileged, optional: install sdl3probe (a headless SDL 3.4 build, LeeNX/sdl3-sinput-probe) for
# tester/sdl3_compare.py. No sudo: it unpacks into ~/sdl3/sdl3-<SDL version>-<tag>/ and points ~/sdl3/current at
# it. Idempotent and quick when that release is already there, so test-matrix.sh runs it before an sinput lane.
#
#   tester/bootstrap-sdl3.sh            # the pinned release below
#   tester/bootstrap-sdl3.sh --check    # only say whether it is installed (exit 1 if not)
#
# The release tag and each tarball's sha256 are pinned here: a new probe release is a reviewed change to this file,
# not whatever GitHub serves today. The tarball needs only libc/libm (glibc <= 2.41) and dlopens libudev; SDL's
# HIDAPI path also needs the hidraw udev rule from tester/bootstrap-sinput.sh.
set -euo pipefail

TAG=v0.2.0
SDL_VERSION=3.4.18
declare -A SHA256=(
  [amd64]=6dc837f8ec292d9d8b1f59d3fe190a33ccbcffd89a4a6de86d45299dda1e0a08
  [arm64]=8906701cf175754a84c762b24e2d12b8b5badb0e1a34008e9c24f1d395188b6b
)
REPO=https://github.com/LeeNX/sdl3-sinput-probe
ROOT=${HIL_SDL3_ROOT:-$HOME/sdl3}

check=0
case ${1:-} in
  --check) check=1 ;;
  "") ;;
  -h | --help) sed -n '2,11p' "$0"; exit 0 ;;
  *) echo "unknown arg: $1" >&2; exit 2 ;;
esac

case $(uname -m) in
  x86_64) arch=amd64 ;;
  aarch64 | arm64) arch=arm64 ;;
  *) echo "sdl3: no release build for $(uname -m)" >&2; exit 2 ;;
esac
dir="$ROOT/sdl3-$SDL_VERSION-$TAG"
probe="$dir/bin/sdl3probe"
if [[ -x $probe && $(readlink "$ROOT/current" 2>/dev/null) == "$dir" ]]; then
  echo "sdl3: $TAG (SDL $SDL_VERSION) installed: $ROOT/current/bin/sdl3probe"
  exit 0
fi
((check)) && { echo "sdl3: $TAG not installed (tester/bootstrap-sdl3.sh)" >&2; exit 1; }

name="sdl3-$SDL_VERSION-trixie-$arch.tar.gz"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL --retry 3 -o "$tmp/$name" "$REPO/releases/download/$TAG/$name"
echo "${SHA256[$arch]}  $tmp/$name" | sha256sum -c --quiet - || { echo "sdl3: $name sha256 mismatch" >&2; exit 1; }
mkdir -p "$tmp/x" "$ROOT"
tar -C "$tmp/x" -xzf "$tmp/$name"
"$tmp/x/sdl3-$SDL_VERSION/bin/sdl3probe" --json --wait 0 >/dev/null 2>&1 || [[ $? == 1 ]] \
  || { echo "sdl3: the unpacked sdl3probe doesn't run" >&2; exit 1; }
rm -rf "$dir"
mv "$tmp/x/sdl3-$SDL_VERSION" "$dir"
ln -sfn "$dir" "$ROOT/current"
echo "sdl3: installed $TAG (SDL $SDL_VERSION, $arch): $ROOT/current/bin/sdl3probe"
