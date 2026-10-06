#!/usr/bin/env bash
# Cut a rig release: bump VERSION, roll the CHANGELOG [Unreleased] section into a
# dated version section, commit "Release vX.Y.Z", and create the annotated tag.
# Pushing the tag is what triggers .github/workflows/release.yml (build the
# firmware matrix, assemble artifacts, publish the GitHub release). See RELEASE.md.
#
#   scripts/release.sh 0.2.0                 # bump + commit + tag, print push cmds
#   scripts/release.sh 0.2.0 --push          # ... and push branch + tag
#   scripts/release.sh 0.2.0 --dry-run
#   scripts/release.sh 0.2.0 --allow-branch  # not on main: release this branch anyway
#   RELEASE_VERSION=0.2.0 scripts/release.sh  # version via env
#
# --remote may be repeated (or RELEASE_REMOTE set to a space/comma-separated
# list) to push the branch + tag to more than one remote, e.g. GitHub and a
# Gitea mirror:  scripts/release.sh 0.2.0 --push --remote origin --remote gitea
#
# Requires a clean working tree so the release commit is only the bump, and the
# release branch (main, or $RELEASE_BRANCH) checked out and not behind the
# remote's: a release cut on a feature branch tags a commit main never gets, and
# main keeps the old VERSION and an [Unreleased] that re-lists what shipped
# (v0.3.0 and v0.3.1 both had to be merged back by hand). --allow-branch (or
# RELEASE_ALLOW_BRANCH=1) releases from the current branch anyway.
set -euo pipefail

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; }

push=false
dry_run=false
allow_branch="${RELEASE_ALLOW_BRANCH:-}"
release_branch="${RELEASE_BRANCH:-main}"
version="${RELEASE_VERSION:-}"
# RELEASE_REMOTE may hold several remotes, space- or comma-separated.
remotes=()
[[ -n "${RELEASE_REMOTE:-}" ]] && read -r -a remotes <<< "${RELEASE_REMOTE//,/ }"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --push) push=true; shift ;;
    --dry-run) dry_run=true; shift ;;
    --allow-branch) allow_branch=1; shift ;;
    --remote) remotes+=("${2:?--remote needs a value}"); shift 2 ;;
    --remote=*) remotes+=("${1#*=}"); shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 1 ;;
    *) version="$1"; shift ;;
  esac
done

[[ -n "$version" ]] || { echo "error: no version (arg or RELEASE_VERSION)" >&2; usage >&2; exit 1; }
if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
  echo "error: '$version' is not MAJOR.MINOR.PATCH[-prerelease]" >&2; exit 1
fi

cd "$(git rev-parse --show-toplevel)"
tag="v$version"
git rev-parse "$tag" >/dev/null 2>&1 && { echo "error: tag $tag already exists" >&2; exit 1; }

if [[ ${#remotes[@]} -eq 0 ]]; then
  remote="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null | cut -d/ -f1 || true)"
  [[ -n "$remote" ]] || { git remote | grep -qx origin && remote=origin; }
  [[ -n "$remote" ]] || { [[ "$(git remote | wc -l | tr -d ' ')" == 1 ]] && remote="$(git remote)"; }
  [[ -n "$remote" ]] && remotes=("$remote")
fi
if [[ ${#remotes[@]} -eq 0 ]]; then
  echo "error: no usable remote; pass --remote NAME. Have:" >&2; git remote -v >&2; exit 1
fi
for remote in "${remotes[@]}"; do
  git remote | grep -qx "$remote" || {
    echo "error: unknown remote '$remote'. Have:" >&2; git remote -v >&2; exit 1
  }
done

if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: working tree not clean:" >&2; git status --short >&2; exit 1
fi

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" != "$release_branch" ]]; then
  if [[ -z "$allow_branch" ]]; then
    {
      echo "error: on '$branch', not '$release_branch'. A release cut here tags a commit $release_branch doesn't have,"
      echo "       and $release_branch keeps the old VERSION and an [Unreleased] listing what already shipped."
      echo "       Merge to $release_branch and release from there, or pass --allow-branch to release from '$branch'."
    } >&2
    exit 1
  fi
  echo "warning: releasing from '$branch', not '$release_branch' (--allow-branch): merge the release commit back to" \
    "$release_branch afterwards, keeping the tagged commit (a merge commit, not a squash)." >&2
else
  # Not behind the remote's release branch: a stale checkout would tag without what's been merged since.
  remote="${remotes[0]}"
  if git fetch -q "$remote" "$release_branch" 2>/dev/null; then
    behind="$(git rev-list --count "HEAD..FETCH_HEAD")"
    ahead="$(git rev-list --count "FETCH_HEAD..HEAD")"
    if [[ "$behind" -gt 0 && -z "$allow_branch" ]]; then
      echo "error: $release_branch is $behind commit(s) behind $remote/$release_branch -- pull first" \
        "(or --allow-branch to release this checkout as it is)" >&2
      exit 1
    fi
    [[ "$ahead" -gt 0 ]] && echo "note: $release_branch is $ahead commit(s) ahead of $remote/$release_branch;" \
      "they go out with the release" >&2
  else
    echo "warning: couldn't fetch $remote/$release_branch to check this checkout is current" >&2
  fi
fi

if ! grep -q '^## \[Unreleased\]' CHANGELOG.md; then
  echo "error: CHANGELOG.md has no '## [Unreleased]' section" >&2; exit 1
fi
# require at least one bullet under [Unreleased]
if ! awk '/^## \[Unreleased\]/{u=1;next} /^## \[/{u=0} u&&/^[-*] /{f=1} END{exit !f}' CHANGELOG.md; then
  echo "error: [Unreleased] is empty -- nothing to release" >&2; exit 1
fi

current="$(cat VERSION)"
today="$(date -u +%Y-%m-%d)"
echo "Current: $current   New: $version   Tag: $tag   Remote(s): ${remotes[*]}"

if $dry_run; then
  echo "(dry run) would: write VERSION, roll CHANGELOG [Unreleased] -> [$version] - $today, commit, tag $tag"
  exit 0
fi

printf '%s\n' "$version" > VERSION

python3 - "$version" "$today" <<'PY'
import re, sys
version, today = sys.argv[1], sys.argv[2]
t = open("CHANGELOG.md").read()
t = t.replace("## [Unreleased]\n",
              f"## [Unreleased]\n\n## [{version}] — {today}\n", 1)
# refresh link refs at the foot of the file
t = re.sub(r"\[Unreleased\]: .*/compare/.*\.\.\.HEAD",
           f"[Unreleased]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/compare/v{version}...HEAD\n"
           f"[{version}]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v{version}",
           t, count=1)
open("CHANGELOG.md", "w").write(t)
PY

git add VERSION CHANGELOG.md
git commit -m "Release $tag"
git tag -a "$tag" -m "$tag"
echo "Committed + tagged $tag on $branch."

if $push; then
  for remote in "${remotes[@]}"; do
    git push "$remote" "$branch" && git push "$remote" "$tag"
    echo "Pushed $branch and $tag to $remote."
  done
  echo "release.yml will build + publish."
else
  for remote in "${remotes[@]}"; do
    echo "Next: git push $remote $branch && git push $remote $tag"
  done
fi
