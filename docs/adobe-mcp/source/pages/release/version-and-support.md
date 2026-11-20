# Version and support

This documentation is for `cavi-adobe-mcp` {{PRODUCT_VERSION}}, released as `{{RELEASE_TAG}}` from commit
`{{RELEASE_COMMIT}}`.

Versions come from git tags. Every change is recorded in the repository
[CHANGELOG.md](https://github.com/cavi-ai/adobe-mcp/blob/main/CHANGELOG.md).

## Requirements

Python 3.15 or newer, on macOS. The servers control the applications through macOS Automation and Accessibility and
Swift helpers, and they are not available on other systems. See [Installation](../introduction/installation.md).

Applications: Photoshop and Illustrator (the newest installed version, unless `CAVI_PHOTOSHOP_APP` or
`CAVI_ILLUSTRATOR_APP` names one), Adobe Animate 2024 and Adobe Dimension.

## What was checked

The unit tests use a scripted native bridge and synthetic files, and the protocol and simulated evaluations do not
start an Adobe application, so none of them establishes an application's own behavior. See [Testing and
evaluation](../reference/testing.md). Every result reports what the application showed, every export is marked
`visual_acceptance` `unreviewed`, and every dispatched command is reported with `retry_safe` `false`.

A tool that fails in your install is a bug to report at [the issue
tracker](https://github.com/cavi-ai/adobe-mcp/issues).

## Other packages

Character Animator is in the separate `cavi-character-animator-mcp` package.

## License and trademarks

MIT licensed.

Adobe, Photoshop, Illustrator, Animate, Dimension, After Effects, Premiere Pro, Substance 3D and Character Animator are
trademarks of Adobe Inc. This project is not affiliated with Adobe. It does not include or redistribute Adobe
software; you supply your own licensed applications.
