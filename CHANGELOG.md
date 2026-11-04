# Changelog

## 0.2.1 - 2026-11-04

### Fixed

- Reconcile an unknown initial Save As only after the matching Character Animator process exits and the destination remains absent.

## Unreleased

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
