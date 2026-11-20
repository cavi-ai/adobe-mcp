# Exports and receipts

`adobe_export` writes a file from an owned copy and a receipt beside it.

| Parameter | Meaning |
|---|---|
| `application`* | `photoshop` or `illustrator` |
| `session_id`* | The owned copy |
| `destination`* | A new path ending in `.png` or `.svg`, in an existing folder |
| `kind` | `png` or `svg`. Default: `png` for Photoshop, `svg` for Illustrator. |
| `usage` | `color` (default) or `data` |

## What each combination does

| Application | `kind` | `usage` | Behavior |
|---|---|---|---|
| Photoshop | `png` | `color` | The document must be 8 or 16 bit RGB with a profile. A duplicate is converted to sRGB and saved as a non-interlaced PNG. The document is not changed. |
| Photoshop | `png` | `data` | RGB or grayscale, 8 or 16 bit. The duplicate is saved without a colour profile and the PNG's colour chunks are removed, so values are not reinterpreted. |
| Illustrator | `svg` | `color` | One artboard, sRGB document, RGB. Text is outlined, raster images are embedded, and the SVG is checked to be self-contained. |
| Illustrator | `png` | `color` or `data` | One artboard, RGB. PNG24 at 100 percent, with transparency. `color` also requires an sRGB document. |

Photoshop SVG export, and SVG with `data`, are refused.

## Checks on the file

The PNG is parsed after export: its signature, chunk checksums, header and end chunk must be valid and its depth 8
or 16 bit. A colour export without an sRGB or ICC declaration gets an sRGB declaration added; the pixel data is not
rewritten. A SVG with a script, `foreignObject`, event handler, entity declaration, stylesheet instruction or an
external reference is rejected. Any failure is an unknown outcome, because the application already ran.

The file is staged in a hidden folder next to the destination, then linked to the destination, so an existing file is
never replaced.

## Result and receipt

The result is the mutation result (`session_id`, `operation_id`, `document`, `copy`, `source_sha256`, `retry_safe`)
with these fields added:

| Field | Meaning |
|---|---|
| `document.export_profile` | The profile the export was converted to, or `null` for `data` and SVG. |
| `document.export_path` | The native path the application wrote. |
| `artifact` | `path`, `sha256`, `bytes`, plus what was checked. A PNG adds `width`, `height`, `bit_depth`, `removed_color_chunks`, `srgb` and `usage`. An SVG adds `usage`, `srgb` and `self_contained`. |
| `native_export_sha256` | The hash of the file as the application wrote it. It differs from `artifact.sha256` when the server added or removed colour chunks. |
| `visual_acceptance` | `unreviewed`. Nothing has compared the picture to what you intended. |
| `receipt` | `path`, `sha256` and `bytes` of the receipt file. |

The receipt file is `<destination>.receipt.json`, for example `out/hero.png.receipt.json`. It holds the same
result, without the `receipt` field. It is created exclusively and is never overwritten.

A pipeline that takes the export should read the receipt, check the `artifact.sha256` against the file and treat
`visual_acceptance` as open. See [Handoffs](../guides/handoffs.md).
