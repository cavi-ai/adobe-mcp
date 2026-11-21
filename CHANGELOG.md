# Changelog

Versions come from git tags; a pull request adds its lines under Unreleased and never edits a version.

## Unreleased

## 0.6.0 - 2026-11-21

### Added

- `photoshop_generative_fill` generates a new Photoshop layer from a prompt inside pixel bounds or across the canvas.
- `photoshop_remove_background` masks one Photoshop pixel layer to its detected subject.
- `photoshop_generative_upscale` doubles an owned PSD of at most 1440 px per side and adds the upscaled image as a new layer.
- `photoshop_text_fonts` lists the style runs of every text layer of an owned PSD and the PostScript fonts Photoshop cannot use.
- `photoshop_set_text_font` sets the font of one Photoshop text layer and refuses a result that keeps another font.
- `illustrator_set_text_font` sets the font of one text frame of an Illustrator layer and refuses a result that keeps another font.
- `photoshop_fonts` reports each font's `source` (`adobe_fonts`, `system`, `local`, `application` or `unknown`) and `file`, the `font_list` it was read from, and takes an optional `family`.
- `photoshop_add_text` and `illustrator_add_text` read the font back after creating the text and refuse a result that keeps another font.

### Removed

- The Character Animator server and its `cavi-character-animator-mcp` command, now in the `cavi-character-animator-mcp` package.
- The `cavi-adobe-authoring-mcp` command, replaced by `cavi-photoshop-mcp` and `cavi-illustrator-mcp`.
- The `application` parameter of the Photoshop and Illustrator tools.

### Changed

- The `adobe_*` tools are now `photoshop_*` on the Photoshop server and `illustrator_*` on the Illustrator server; the Illustrator group tool is `illustrator_add_layer`.
- `media_review_movie` is served by the Animate server.
- Modules are grouped by application: `cavi_adobe_mcp.photoshop`, `.illustrator`, `.animate` and `.dimension`, with shared code in `.core` and frame and movie code in `.media`.
- Requires `cavi-mcp-core[media]` 0.1.8; the workspace root, path confinement, file digests, file locks, bounded commands, program lookup, private record reads and scratch removal come from it.
- A timed-out `osascript`, `open`, `swiftc` or native helper command is stopped with every process it started.
- Tool results are built on the `cavi-mcp-core` `Result` model: an optional field the tool did not set is left out instead of sent as `null`.
- Photoshop and Illustrator errors are `cavi-mcp-core` `OperationRejected` and `OperationUnknown`.

### Fixed

- `media_review_movie` finds `ffmpeg` and `ffprobe` where Homebrew installs them when the server's `PATH` leaves that directory out.
- The 0.4.0 section lists four Character Animator fixes that were filed under a second Unreleased heading.
- The Photoshop and Illustrator tool pages link to Results and errors inside the documentation.
- The documentation check refuses a page link that names no documentation file.
- The release sends the documentation to cavi-home with the `CAVI_DISPATCH_TOKEN` secret.

## 0.5.0 - 2026-11-16

### Changed

- Frame receipts and bounded native runs come from `cavi-mcp-core[media]` 0.1.4.
- The README lists the After Effects, Premiere and Substance packages.

### Removed

- The `after_effects_render` tool, now in `cavi-after-effects-mcp`; the native gate exports generated frames and no longer takes `--ae-recipe`.

## 0.4.0 - 2026-11-09

### Added

- Select Photoshop, Illustrator, and Animate bundles with application-specific environment overrides.
- Abandon an unknown Character Animator operation after its application instances exit.

### Changed

- Requires Python 3.15 or newer.
- Use MCP Core application discovery and report unavailable or mismatched application bundles explicitly.

### Fixed

- Preserve Character Animator import and export chooser state and verify export-folder navigation.
- Release Character Animator ownership after application exit when no operation is pending.
- Retain completed Animate operation receipts and reject undeclared tool arguments.
- Use nonblocking project-file inspection for Character Animator ownership checks.
- Retain bounded attempt diagnostics after interrupted native chooser calls.
- Keep Character Animator ownership responses stable while opening a prepared project copy.
- Activate the exact owned background chooser before continuing its recorded operation.

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
