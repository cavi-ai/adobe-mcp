# Overview

`cavi-adobe-mcp` provides three MCP servers for Adobe applications on macOS. Each edits a copy it owns, keeps the
layers, paths or timelines editable, and records every native operation before it runs.

| Server command | Applications | What it does |
|---|---|---|
| `cavi-adobe-authoring-mcp` | Photoshop, Illustrator | Opens owned PSD or AI copies, edits layers, text, paths and masks, runs Photoshop's generative tools, exports PNG or SVG, and makes review movies with FFmpeg. |
| `cavi-animate-mcp` | Animate | Opens owned FLA copies, authors timelines, and exports the frames with a media receipt. |
| `cavi-dimension-mcp` | Dimension | Starts a render of a saved scene and verifies the rendered PNG. |

Character Animator is not part of this package. It is in the separate `cavi-character-animator-mcp` package.

The servers run on macOS with the Adobe applications installed, licensed and signed in by you. Starting a server
never launches an application, signs in or requests a permission. Only `dimension_launch` starts an application.

## Photoshop and Illustrator tools

The authoring server has 20 tools.

| Group | Tools |
|---|---|
| Read | `adobe_status`, `adobe_inspect`, `adobe_fonts` |
| Open and close | `adobe_open_copy`, `adobe_create_canvas`, `adobe_close_copy` |
| Edit | `adobe_resize_image`, `adobe_edit_layers`, `adobe_add_group`, `adobe_place_image`, `adobe_add_rectangle`, `adobe_add_path`, `adobe_add_text`, `adobe_apply_mask` |
| Photoshop generative | `adobe_generative_fill`, `adobe_remove_background`, `adobe_generative_upscale` |
| Export and recovery | `adobe_export`, `adobe_operation_control` |
| Review movies | `media_review_movie` |

The three generative tools were added most recently. Generative Fill and Generative Upscale use the signed-in
Creative Cloud account, send pixels to Adobe and spend generative credits. See [Generative
tools](../photoshop-illustrator/generative.md).

## How a call works

1. Every path is relative to one workspace directory, set by `CAVI_ADOBE_WORKSPACE`.
2. A tool that opens a copy returns a `session_id`. Later edits name that session. Edits never touch the source file.
3. A tool that changes the application writes the operation to a journal before it dispatches, then records the
   result. A result carries `retry_safe` `false`.
4. If a native command may have run but did not report back, the outcome is unknown and later changes stop until you
   inspect the application and resolve the operation. See [Recovery and unknown outcomes](../guides/recovery.md).

## Next

- [Installation](installation.md) and [Quickstart](quickstart.md)
- [Photoshop and Illustrator tools](../photoshop-illustrator/tools.md), [Animate tools](../animate/tools.md) and
  [Dimension tools](../dimension/tools.md)
- [Handoffs](../guides/handoffs.md): continuing from a result in another tool or pipeline
