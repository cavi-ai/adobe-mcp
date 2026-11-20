# Photoshop tools

The Photoshop server (`cavi-photoshop-mcp`, MCP server name `cavi-adobe-photoshop`) has 21 tools. A parameter marked
`*` is required. Units are pixels, measured from the top left.

## Shared rules

- A layer is addressed by `path`, a list of 1 to 32 zero-based indices (each 0 to 10000) from the document root,
  together with `expected_name`. If the layer at that path has another name, the edit is refused.
- Layer, group, path and font names are 1 to 128 printable characters.
- A locked layer or group that an edit targets is refused.
- Every tool that changes a document works on an owned copy named by `session_id`, and returns the mutation result
  described in [Results and errors](../../reference/results-and-errors.md).
- Photoshop accepts one command at a time. A second call while one runs is refused.
- Commands time out after 45 seconds, 120 seconds for the three generative tools. A timeout is an unknown outcome.

## Read

| Tool | Parameters | Result |
|---|---|---|
| `photoshop_status` | none | `app`, `application` (the `.app` path), `version`, `running`, `process`, `launches_application` `false`, `authenticates` `false`. Reads the installation and the process only. |
| `photoshop_inspect` | `session_id` | Without a session: the `version` and every open document (up to 64) with its layers, plus `runtime` and `pending_operation`. With a `session_id`: the snapshot of that owned copy and the stored `session` record. |
| `photoshop_fonts` | `family` | `fonts`, each with `name`, `family`, `style`, `postscript_name`, `source` and `file`, and `font_list`. See [Fonts](fonts.md). |
| `photoshop_text_fonts` | `session_id`* | `layers`, each text layer with `path`, `name`, `native_id` and its style `runs`, and `missing_fonts`. See [Fonts](fonts.md). |

A document snapshot has `name`, `path`, `native_id`, `identity`, `saved`, `width`, `height`, `units`,
`color_profile`, `color_space`, `bit_depth`, `layers` and `paths`. Each layer has `path`, `name`, `visible`,
`opacity`, `kind`, `id`, `locked`, `has_user_mask` and its text. A snapshot lists at most 2048 layers nested at most
32 deep.

## Open and close

| Tool | Parameters | Notes |
|---|---|---|
| `photoshop_open_copy` | `source`*, `destination`* | Reads `.psd` or `.png` and writes `.psd`. The destination must be new and its folder must exist. |
| `photoshop_create_canvas` | `width`*, `height`* (1 to 2880), `destination`*, `bit_depth` (8 or 16, default 16) | Creates a transparent 72 ppi sRGB RGB document saved as `.psd`. The result adds `source_kind` `canvas_recipe`, `source_sha256_kind` `canvas_recipe_json` and the `canvas_recipe` artifact. |
| `photoshop_close_copy` | `session_id`* | Closes the owned document without saving. Each operation saves the copy before it returns. |

See [Owned copies](../guides/owned-copies.md).

## Edit

| Tool | Parameters | Notes |
|---|---|---|
| `photoshop_resize_image` | `session_id`*, `width`*, `height`* (1 to 2880) | Resamples the document with bicubic sharper and keeps layers and paths. |
| `photoshop_edit_layers` | `session_id`*, `edits`* (1 to 64) | Each edit has `path`*, `expected_name`* and at least one of `name`, `visible`, `opacity` (0 to 100). Paths must be unique. `changed_layers` lists every edited layer. |
| `photoshop_add_group` | `session_id`*, `name`*, `parent_path`, `expected_parent_name` | A layer set, at the root or inside the named group. |
| `photoshop_place_image` | `session_id`*, `source`*, `name`*, `position`*, `group_path`, `expected_group_name` | Embeds a workspace PNG on a new layer. No link to the file is kept. `position` is `[x, y]` from 0 to 30000. The result adds `placement_source`. |
| `photoshop_add_rectangle` | `session_id`*, `name`*, `bounds`*, `rgb`*, `opacity` (0 to 100, default 100) | A filled rectangle on a new layer. `bounds` is `[x, y, width, height]` and must lie inside the document. `rgb` is three values from 0 to 255. |
| `photoshop_add_path` | `session_id`*, `name`*, `points`* (2 to 1024), `closed` (default `true`), `rgb`, `group_path`, `expected_group_name` | Editable Bézier geometry. Each point has `anchor`, `left` and `right` pairs from -30000 to 30000. With `rgb` the path is filled and must be closed. A path name must not already exist. |
| `photoshop_add_text` | `session_id`*, `name`*, `text`*, `font`*, `position`*, `size`*, `rgb`* | Native text. `text` is 1 to 10000 characters. `font` is an exact PostScript name from `photoshop_fonts`; a layer that ends up with another or an unavailable font in any style run is refused. `size` is 0.1 to 1296 points. |
| `photoshop_set_text_font` | `session_id`*, `layer_path`*, `expected_name`*, `font`* | Gives every style run of one unlocked text layer the font. `font` is an exact PostScript name from `photoshop_fonts`. See [Fonts](fonts.md). |
| `photoshop_apply_mask` | `session_id`*, `layer_path`*, `expected_name`*, `path_name`* | Applies the one closed path with that name as a layer mask. A layer that already has a mask is refused. |

`photoshop_add_rectangle`, `photoshop_add_path`, `photoshop_add_text`, `photoshop_place_image` and the generative
tools require an RGB document.

## Generative

| Tool | Parameters | Notes |
|---|---|---|
| `photoshop_generative_fill` | `session_id`*, `prompt`*, `bounds` | A new layer generated from the prompt. |
| `photoshop_remove_background` | `session_id`*, `layer_path`*, `expected_name`* | A layer mask that keeps the detected subject. |
| `photoshop_generative_upscale` | `session_id`* | Doubles the document and adds a layer named `Generative Upscale`. |

Limits, credits and the one-time notice are on [Generative tools](generative.md).

## Export and recovery

| Tool | Parameters | Notes |
|---|---|---|
| `photoshop_export` | `session_id`*, `destination`* (new `.png`), `usage` (`color` or `data`, default `color`) | See [Exports and receipts](../guides/exports-and-receipts.md). |
| `photoshop_operation_control` | `session_id`*, `operation_id`*, `action` (`inspect`, `adopt_current` or `abandon_after_exit`, default `inspect`), `expected_copy_sha256`, `expected_native_sha256` | See [Recovery and unknown outcomes](../guides/recovery.md). |
