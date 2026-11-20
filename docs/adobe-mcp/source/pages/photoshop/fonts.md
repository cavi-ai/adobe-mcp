# Photoshop fonts

Three Photoshop tools work with fonts: `photoshop_fonts`, `photoshop_text_fonts` and `photoshop_set_text_font`.
`photoshop_add_text` also checks the font it applies.

No tool activates, deactivates or downloads a font. Adobe Fonts are activated in Creative Cloud, in the Creative Cloud
desktop app or on fonts.adobe.com. Activate the font there, then call `photoshop_fonts` again. Photoshop must be running.

## `photoshop_fonts`

| Parameter | Meaning |
|---|---|
| `family` | Keep only the fonts of one family. The match is exact and case-insensitive. 1 to 128 printable characters. |

Each entry of `fonts` has:

| Field | Meaning |
|---|---|
| `name`, `family`, `style` | As Photoshop reports them |
| `postscript_name` | The exact name to pass as `font` to `photoshop_add_text` and `photoshop_set_text_font` |
| `source` | Where the font comes from, below |
| `file` | The font file path from Photoshop's font list, or `null` |

The result also has `font_list`, `{path, modified}` for the font list file that was read (`modified` is a UTC
timestamp), or `null` when Photoshop has not written one. `runtime` and `pending_operation` are as for
`photoshop_inspect`.

### `source`

| Value | The font file lies in |
|---|---|
| `adobe_fonts` | `~/Library/Application Support/Adobe/CoreSync/plugins/livetype/.r/` or `.w/`: fonts activated in Creative Cloud |
| `system` | `/System/Library/` |
| `local` | `/Library/Fonts/` or `~/Library/Fonts/` |
| `application` | The Photoshop application bundle |
| `unknown` | Any other location, or the font has no record in the font list |

Adobe Fonts are told apart from the other fonts by the location of their font file, which Photoshop records in its font
list, `~/Library/Application Support/Adobe/Adobe Photoshop <year>/CT Font Cache/AdobeFnt_OSFonts.lst`. The list belongs
to the installed Photoshop the server resolves. The server reads only that text file. It never opens a font file.
`adobe_fonts` therefore relies on that list: a font Photoshop lists but the list has no record for is reported as
`unknown`, never guessed, and without a list file every `source` is `unknown` and `file` is `null`.

The list is refused when it is over 64 MB, is not UTF-8 text, is not a regular file or does not begin with
`%!Adobe-FontList`.

## `photoshop_text_fonts`

| Parameter | Meaning |
|---|---|
| `session_id`* | The owned copy |

Reads the style runs of every text layer, with the same ownership checks as `photoshop_inspect` on a session. The
result has:

| Field | Meaning |
|---|---|
| `layers` | Each text layer: `path`, `name`, `native_id` and `runs` |
| `runs` | Each run: `from` and `to` (character offsets), `postscript_name` and `available`, whether Photoshop can use the font |
| `missing_fonts` | The sorted, unique PostScript names of runs whose font is not available |

A run whose font Photoshop cannot use shows `available` `false`. At most 2048 layers and 4096 runs are read; a larger document is refused. Nothing in the
document changes.

## `photoshop_set_text_font`

| Parameter | Meaning |
|---|---|
| `session_id`* | The owned copy |
| `layer_path`*, `expected_name`* | The one text layer, as in [Photoshop tools](tools.md) |
| `font`* | An exact PostScript name from `photoshop_fonts`, 1 to 128 printable characters |

Every run of the layer gets the font. The result is the mutation result, with the layer in `changed_layers`.

| Condition | Result |
|---|---|
| Invalid `session_id`, `layer_path`, `expected_name` or `font` | Refused before dispatch |
| The layer is not a text layer, or is locked | Refused |
| `font` is not in Photoshop's font list | Refused: `Exact PostScript font is unavailable; use fonts discovery` |
| After the assignment a run reports another font or `available` `false` | Unknown outcome, because the document may already have changed. Resolve it with [Recovery and unknown outcomes](../guides/recovery.md). |

Photoshop does not raise an error when it cannot use a font: the layer silently keeps another one. The tool therefore
reads every run back after the assignment.

## `photoshop_add_text`

After the layer is created, every style run must report the requested font as available. A layer that keeps another
font is an unknown outcome, as for `photoshop_set_text_font`.
