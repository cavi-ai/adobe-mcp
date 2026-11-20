# Photoshop and Illustrator tools

The authoring server (`cavi-adobe-authoring-mcp`) has 20 tools. `application` is `photoshop` or `illustrator`
unless a tool says otherwise. A parameter marked `*` is required. Units are pixels in Photoshop and points in
Illustrator, measured from the top left.

## Shared rules

- A layer is addressed by `path`, a list of 1 to 32 zero-based indices (each 0 to 10000) from the document root,
  together with `expected_name`. If the layer at that path has another name, the edit is refused.
- Layer, group, path and font names are 1 to 128 printable characters.
- A locked layer or group that an edit targets is refused.
- Every tool that changes a document works on an owned copy named by `session_id`, and returns the mutation result
  described in [Results and errors](../reference/results-and-errors.md).
- Photoshop and Illustrator accept one command at a time. A second call while one runs is refused.
- Commands time out after 45 seconds, 120 seconds for the three generative tools. A timeout is an unknown outcome.

## Read

| Tool | Parameters | Result |
|---|---|---|
| `adobe_status` | `application`* | `app`, `application` (the `.app` path), `version`, `running`, `process`, `launches_application` `false`, `authenticates` `false`. Reads the installation and the process only. |
| `adobe_inspect` | `application`*, `session_id` | Without a session: the `version` and every open document (up to 64) with its layers, plus `runtime` and `pending_operation`. With a `session_id`: the snapshot of that owned copy and the stored `session` record. |
| `adobe_fonts` | `application`* | `fonts`, each with `name`, `family`, `style` and `postscript_name`. |

A document snapshot has `name`, `path`, `native_id`, `identity`, `saved`, `width`, `height`, `units`,
`color_profile`, `color_space`, `bit_depth`, `layers` and `paths`. Each layer has `path`, `name`, `visible`,
`opacity`, `kind`, `id`, `locked` and its text. Photoshop layers also report `has_user_mask`; Illustrator layers
report `clipping_groups`. A snapshot lists at most 2048 layers nested at most 32 deep.

## Open and close

| Tool | Parameters | Notes |
|---|---|---|
| `adobe_open_copy` | `application`*, `source`*, `destination`* | Photoshop reads `.psd` or `.png` and writes `.psd`. Illustrator reads `.ai`, `.svg` or `.png` and writes `.ai`. The destination must be new and its folder must exist. |
| `adobe_create_canvas` | `width`*, `height`* (1 to 2880), `destination`*, `bit_depth` (8 or 16, default 16) | Photoshop only. Creates a transparent 72 ppi sRGB RGB document saved as `.psd`. The result adds `source_kind` `canvas_recipe`, `source_sha256_kind` `canvas_recipe_json` and the `canvas_recipe` artifact. |
| `adobe_close_copy` | `application`*, `session_id`* | Closes the owned document. Each operation saves the copy before it returns. |

See [Owned copies](owned-copies.md).

## Edit

| Tool | Parameters | Notes |
|---|---|---|
| `adobe_resize_image` | `application`* (`photoshop`), `session_id`*, `width`*, `height`* (1 to 2880) | Resamples the document with bicubic sharper and keeps layers and paths. |
| `adobe_edit_layers` | `application`*, `session_id`*, `edits`* (1 to 64) | Each edit has `path`*, `expected_name`* and at least one of `name`, `visible`, `opacity` (0 to 100). Paths must be unique. `changed_layers` lists every edited layer. |
| `adobe_add_group` | `application`*, `session_id`*, `name`*, `parent_path`, `expected_parent_name` | A Photoshop layer set or an Illustrator sublayer, at the root or inside the named group. |
| `adobe_place_image` | `application`*, `session_id`*, `source`*, `name`*, `position`*, `group_path`, `expected_group_name` | Embeds a workspace PNG on a new layer. No link to the file is kept. `position` is `[x, y]` from 0 to 30000. The result adds `placement_source`. |
| `adobe_add_rectangle` | `application`*, `session_id`*, `name`*, `bounds`*, `rgb`*, `opacity` (0 to 100, default 100) | A filled rectangle on a new layer. `bounds` is `[x, y, width, height]` and must lie inside the document. `rgb` is three values from 0 to 255. |
| `adobe_add_path` | `application`*, `session_id`*, `name`*, `points`* (2 to 1024), `closed` (default `true`), `rgb`, `group_path`, `expected_group_name` | Editable Bézier geometry. Each point has `anchor`, `left` and `right` pairs from -30000 to 30000. With `rgb` the path is filled and must be closed. A Photoshop path name must not already exist. |
| `adobe_add_text` | `application`*, `session_id`*, `name`*, `text`*, `font`*, `position`*, `size`*, `rgb`* | Native text. `text` is 1 to 10000 characters. `font` is an exact PostScript name from `adobe_fonts`; Photoshop refuses a substituted font. `size` is 0.1 to 1296 points. |
| `adobe_apply_mask` | `application`*, `session_id`*, `layer_path`*, `expected_name`*, `path_name`* | Applies the one closed path with that name as a Photoshop layer mask, or as an Illustrator clipping group. A Photoshop layer that already has a mask is refused. |

`adobe_add_rectangle`, `adobe_add_path`, `adobe_add_text`, `adobe_place_image` and the generative tools require an
RGB document. `adobe_create_canvas`, `adobe_resize_image` and the generative tools are refused for Illustrator
before anything is sent to the application.

## Photoshop generative

| Tool | Parameters | Notes |
|---|---|---|
| `adobe_generative_fill` | `application`* (`photoshop`), `session_id`*, `prompt`*, `bounds` | A new layer generated from the prompt. |
| `adobe_remove_background` | `application`* (`photoshop`), `session_id`*, `layer_path`*, `expected_name`* | A layer mask that keeps the detected subject. |
| `adobe_generative_upscale` | `application`* (`photoshop`), `session_id`* | Doubles the document and adds a layer named `Generative Upscale`. |

Limits, credits and the one-time notice are on [Generative tools](generative.md).

## Export and recovery

| Tool | Parameters | Notes |
|---|---|---|
| `adobe_export` | `application`*, `session_id`*, `destination`*, `kind` (`png` or `svg`), `usage` (`color` or `data`, default `color`) | See [Exports and receipts](exports-and-receipts.md). |
| `adobe_operation_control` | `application`*, `session_id`*, `operation_id`*, `action` (`inspect`, `adopt_current` or `abandon_after_exit`, default `inspect`), `expected_copy_sha256`, `expected_native_sha256` | See [Recovery and unknown outcomes](../guides/recovery.md). |

## Review movies

| Tool | Parameters | Notes |
|---|---|---|
| `media_review_movie` | `receipt`*, `directory`*, `codec` (`prores_alpha` or `h264_black`, default `prores_alpha`) | Free local FFmpeg. See [Review movies](../guides/review-movies.md). |
