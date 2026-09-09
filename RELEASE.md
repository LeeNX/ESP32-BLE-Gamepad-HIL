# Rig release process

The rig is versioned independently of the ESP32-BLE-Gamepad library (see the
[CHANGELOG](CHANGELOG.md) intro for what a bump means). A release is a git tag, a
GitHub Release, and two attached tarballs:

- **`esp32-ble-gamepad-hil-firmware-vX.Y.Z.tar.gz`** — the full `board × profile`
  firmware-bundle set (prebuilt `.bin`s + manifests), the golden HID descriptors,
  and `index.json` (rig + library provenance). This is what lets anyone
  reproduce a run without the rig — see [REPRODUCE.md](REPRODUCE.md).
- **`esp32-ble-gamepad-hil-suite-vX.Y.Z.tar.gz`** — a standalone copy of the
  pytest suite + `tester/` scripts.

## Cutting a release

1. Land everything for the release on `main`. Move the `## [Unreleased]` bullets
   in [CHANGELOG.md](CHANGELOG.md) into shape (they become the release body
   alongside GitHub's auto-generated notes).
2. Run the latency/throughput sweep on `main` — it's no longer on every push.
   Trigger [`hil.yml`](.github/workflows/hil.yml) via the Actions tab (or
   `gh workflow run hil.yml -f bench=true`), or just take the most recent Monday
   `schedule` run if `main` hasn't moved since. Regenerate
   `docs/bench/bench-table.md` + SVGs from its `results/` if the numbers moved.
3. From a clean `main`:

   ```bash
   scripts/release.sh 0.2.0            # bump VERSION, roll the changelog, commit, tag
   git push origin main && git push origin v0.2.0
   # or: scripts/release.sh 0.2.0 --push
   ```

   `scripts/release.sh` refuses a dirty tree, an existing tag, or an empty
   `[Unreleased]` section.
4. Pushing the `v*` tag triggers [`.github/workflows/release.yml`](.github/workflows/release.yml):
   it checks `VERSION` matches the tag, builds the firmware matrix against the
   library (repo variable `HIL_LIB_REF`, else `master` — override with the
   `lib_ref` input on a manual re-run), runs `scripts/make-release-artifacts.sh`,
   and publishes the release with `--generate-notes`. A `-rcN` / `-beta` suffix
   marks it a prerelease.

   > `hil_runner` needs the library's HID report-descriptor getters, which are
   > on a fork branch until merged to `master`. Set repo variable `HIL_LIB_REF`
   > to that branch before the first release, and clear it once merged.

## Pre-release / dry run

```bash
scripts/release.sh 0.2.0 --dry-run           # show what it would change
./run.sh --profiles default                  # build a bundle or two locally
scripts/make-release-artifacts.sh v0.2.0-test # assemble dist/ without tagging
```

## After the tag

The library's `release.yml` rebuilds the same firmware set from the rig ref
pinned in its `HIL_RIG_REF` repo variable and attaches it to the **library's**
GitHub Release too, so a library release carries the firmware it was validated
with. Bump `HIL_RIG_REF` to the new rig tag when you want the library to pick it
up.
