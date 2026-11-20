# Illustrator tools

The Illustrator server (`cavi-illustrator-mcp`, MCP server name `cavi-adobe-illustrator`) has 15 tools. A parameter
marked `*` is required. Units are points, measured from the top left.

## Shared rules

- A layer is addressed by `path`, a list of 1 to 32 zero-based indices (each 0 to 10000) from the document root,
  together with `expected_name`. If the layer at that path has another name, the edit is refused.
- Layer, path and font names are 1 to 128 printable characters.
- A locked layer or group that an edit targets is refused.
- Every tool that changes a document works on an owned copy named by `session_id`, and returns the mutation result
  described in [Results and errors](../../reference/results-and-errors.md).
- Illustrator accepts one command at a time. A second call while one runs is refused.
- Commands time out after 45 seconds. A timeout is an unknown outcome.

## Read

| Tool | Parameters | Result |
|---|---|---|
| `illustrator_status` | none | `app`, `application` (the `.app` path), `version`, `running`, `process`, `launches_application` `false`, `authenticates` `false`. Reads the installation and the process only. |
| `illustrator_inspect` | `session_id` | Without a session: the `version` and every open document (up to 64) with its layers, plus `runtime` and `pending_operation`. With a `session_id`: the snapshot of that owned copy and the stored `session` record. |
| `illustrator_fonts` | none | `fonts`, each with `name`, `family`, `style` and `postscript_name`. |

A document snapshot has `name`, `path`, `native_id`, `identity`, `saved`, `width`, `height`, `units`,
`color_profile`, `color_space`, `bit_depth`, `layers` and `paths`. Each layer has `path`, `name`, `visible`,
`opacity`, `kind`, `id`, `locked`, `clipping_groups` and its text. A snapshot lists at most 2048 layers nested at
most 32 deep.

## Open and close

| Tool | Parameters | Notes |
|---|---|---|
| `illustrator_open_copy` | `source`*, `destination`* | Reads `.ai`, `.svg` or `.png` and writes `.ai`. The destination must be new and its folder must exist. |
| `illustrator_close_copy` | `session_id`* | Closes the owned document without saving. Each operation saves the copy before it returns. |

See [Owned copies](../guides/owned-copies.md).

## Edit

| Tool | Parameters | Notes |
|---|---|---|
| `illustrator_edit_layers` | `session_id`*, `edits`* (1 to 64) | Each edit has `path`*, `expected_name`* and at least one of `name`, `visible`, `opacity` (0 to 100). Paths must be unique. `changed_layers` lists every edited layer. |
| `illustrator_add_layer` | `session_id`*, `name`*, `parent_path`, `expected_parent_name` | A sublayer, at the root or inside the named layer. |
| `illustrator_place_image` | `session_id`*, `source`*, `name`*, `position`*, `group_path`, `expected_group_name` | Embeds a workspace PNG on a new layer. No link to the file is kept. `position` is `[x, y]` from 0 to 30000. The result adds `placement_source`. |
| `illustrator_add_rectangle` | `session_id`*, `name`*, `bounds`*, `rgb`*, `opacity` (0 to 100, default 100) | A filled rectangle on a new layer. `bounds` is `[x, y, width, height]` and must lie inside the document. `rgb` is three values from 0 to 255. |
| `illustrator_add_path` | `session_id`*, `name`*, `points`* (2 to 1024), `closed` (default `true`), `rgb`, `group_path`, `expected_group_name` | Editable Bézier geometry. Each point has `anchor`, `left` and `right` pairs from -30000 to 30000. With `rgb` the path is filled and must be closed. |
| `illustrator_add_text` | `session_id`*, `name`*, `text`*, `font`*, `position`*, `size`*, `rgb`* | Native text. `text` is 1 to 10000 characters. `font` is an exact PostScript name from `illustrator_fonts`; a frame that ends up with another font is refused. `size` is 0.1 to 1296 points. |
| `illustrator_set_text_font` | `session_id`*, `layer_path`*, `expected_name`*, `frame_index`*, `font`* | Sets the font of one text frame of an unlocked layer. `frame_index` is the zero-based index among the layer's direct text frames. `font` is an exact name from `illustrator_fonts`. See [Fonts](fonts.md). |
| `illustrator_apply_mask` | `session_id`*, `layer_path`*, `expected_name`*, `path_name`* | Applies the one closed path with that name as a clipping group. |

`illustrator_add_rectangle`, `illustrator_add_path`, `illustrator_add_text` and `illustrator_place_image` require an
RGB document.

## Export and recovery

| Tool | Parameters | Notes |
|---|---|---|
| `illustrator_export` | `session_id`*, `destination`* (new `.png` or `.svg`), `kind` (`png` or `svg`, default `svg`), `usage` (`color` or `data`, default `color`) | See [Exports and receipts](../guides/exports-and-receipts.md). |
| `illustrator_operation_control` | `session_id`*, `operation_id`*, `action` (`inspect`, `adopt_current` or `abandon_after_exit`, default `inspect`), `expected_copy_sha256`, `expected_native_sha256` | See [Recovery and unknown outcomes](../guides/recovery.md). |
