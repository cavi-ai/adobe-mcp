# Overview

`cavi-adobe-mcp` provides four MCP servers for Adobe applications on macOS. Each edits a copy it owns, keeps the
layers, paths or timelines editable, and records every native operation before it runs.

| Server command | Application | What it does |
|---|---|---|
| `cavi-photoshop-mcp` | Photoshop | Opens owned PSD copies or new canvases, edits layers, text, paths and masks, runs the generative tools, and exports PNG. 21 tools. |
| `cavi-illustrator-mcp` | Illustrator | Opens owned AI copies, edits layers, text, paths and masks, and exports SVG or PNG. 15 tools. |
| `cavi-animate-mcp` | Animate | Opens owned FLA copies, authors timelines, exports the frames with a media receipt, and makes review movies with FFmpeg. 10 tools. |
| `cavi-dimension-mcp` | Dimension | Starts a render of a saved scene and verifies the rendered PNG. |

Character Animator is not part of this package. It is in the separate `cavi-character-animator-mcp` package.

The servers run on macOS with the Adobe applications installed, licensed and signed in by you. Starting a server
never launches an application, signs in or requests a permission. Only `dimension_launch` starts an application.

## Photoshop and Illustrator tools

Each tool name starts with its application, and no tool takes an `application` parameter.

| Group | Photoshop (21) | Illustrator (15) |
|---|---|---|
| Read | `photoshop_status`, `photoshop_inspect`, `photoshop_fonts`, `photoshop_text_fonts` | `illustrator_status`, `illustrator_inspect`, `illustrator_fonts` |
| Open and close | `photoshop_open_copy`, `photoshop_create_canvas`, `photoshop_close_copy` | `illustrator_open_copy`, `illustrator_close_copy` |
| Edit | `photoshop_resize_image`, `photoshop_edit_layers`, `photoshop_add_group`, `photoshop_place_image`, `photoshop_add_rectangle`, `photoshop_add_path`, `photoshop_add_text`, `photoshop_set_text_font`, `photoshop_apply_mask` | `illustrator_edit_layers`, `illustrator_add_layer`, `illustrator_place_image`, `illustrator_add_rectangle`, `illustrator_add_path`, `illustrator_add_text`, `illustrator_set_text_font`, `illustrator_apply_mask` |
| Generative | `photoshop_generative_fill`, `photoshop_remove_background`, `photoshop_generative_upscale` | none |
| Export and recovery | `photoshop_export`, `photoshop_operation_control` | `illustrator_export`, `illustrator_operation_control` |

`media_review_movie` is served by the Animate server.

Generative Fill and Generative Upscale use the signed-in Creative Cloud account, send pixels to Adobe and spend
generative credits. See [Generative tools](../photoshop/generative.md).

## How a call works

1. Every path is relative to one workspace directory, set by `CAVI_ADOBE_WORKSPACE`.
2. A tool that opens a copy returns a `session_id`. Later edits name that session. Edits never touch the source file.
3. A tool that changes the application writes the operation to a journal before it dispatches, then records the
   result. A result carries `retry_safe` `false`.
4. If a native command may have run but did not report back, the outcome is unknown and later changes stop until you
   inspect the application and resolve the operation. See [Recovery and unknown outcomes](../guides/recovery.md).

## Next

- [Installation](installation.md) and [Quickstart](quickstart.md)
- [Photoshop tools](../photoshop/tools.md), [Illustrator tools](../illustrator/tools.md),
  [Animate tools](../animate/tools.md) and [Dimension tools](../dimension/tools.md)
- [Handoffs](../guides/handoffs.md): continuing from a result in another tool or pipeline
