# Changelog

Versions come from git tags; a pull request adds its lines under Unreleased and never edits a version.

## Unreleased

### Added

- `CAVI_PHOTOSHOP_APP`, `CAVI_ILLUSTRATOR_APP` and `CAVI_ANIMATE_APP` choose the application bundle; an override naming another application is refused with `APP_IDENTITY_MISMATCH`.

### Changed

- The package version is read from the release tag.
- Requires Python 3.15 or newer; CI lints on Linux and runs the unit tests once, on macOS with Python 3.15, after a first `pin-guard` job that fails any change adding or moving a submodule or git dependency; a green `main` dispatches `server-merged` to game-art-pipeline, whose pin bot moves it to that commit. Dialogue and editorial WAV input still refuses float PCM, which Python 3.15's `wave` now reads.
- Every server finds its application through `cavi-mcp-core` application lookup; without an override the newest installed version is used, including for Character Animator.
- A missing Photoshop or Illustrator reports `APP_UNAVAILABLE` instead of `INPUT_NOT_FOUND`.

## 0.3.1 - 2026-11-04

### Added

- Abandon an unknown operation only after the owned Character Animator process and all same-bundle instances exit.

## 0.3.0 - 2026-11-04

### Added

- Prepare, open and claim a verified workspace copy of a closed Character Animator project.
## 0.2.3 - 2026-11-04

### Fixed

- The shipped Swift helper build test runs only on macOS, so Linux CI and release runs with a Swift toolchain pass.

## 0.2.2 - 2026-11-04

### Changed

- Releases build and test on Ubuntu runners; the unit matrix also runs on Ubuntu.

## 0.2.1 - 2026-11-04

### Fixed

- Reconcile an unknown initial Save As only after the matching Character Animator process exits and the destination remains absent.

## Unreleased

### Fixed

- Release an owned Character Animator lease after app exit when no operation is pending.
- Use nonblocking project-file inspection for Character Animator ownership checks.
- Retain bounded attempt diagnostics after interrupted native chooser calls.
- Keep Character Animator ownership responses stable while opening a prepared project copy.
- Activate the exact owned background chooser before continuing its recorded operation.

## 0.2.0 - 2026-11-04

### Added

- Photoshop can resample an owned document to exact pixel dimensions while retaining editable layers and paths.

### Fixed

- Character Animator reactivates a background owned chooser only after checking the expected project, process, and chooser.
- Character Animator export continuation resumes the recorded chooser step and verifies a stable chooser state before the next command.

## 0.1.1 - 2026-11-04

### Fixed

- Reconcile recorded initial Save As directory requests after stable source and absent-destination checks.
- Validate initial project-copy paths before continuing a pending save.

## 0.1.0 - 2026-11-04

### Added

- Authoring server for Photoshop and Illustrator copies, new sRGB canvases, After Effects `aerender` jobs and FFmpeg review movies.
- Animate server for owned FLA documents, timeline edits and ordered frame exports.
- Character Animator server for native project state, owned copies, scene actions and PNG/WAV export intake.
- Dimension server for owned DN copies and native PNG renders.
