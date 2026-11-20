# Handoffs

A result from these servers is meant to be continued by another tool or pipeline: a script, another MCP server or a
person. This page lists what to take from each result and what to check before using it. Nothing here is verified by
looking at the picture: every export reports `visual_acceptance` `unreviewed`.

## Photoshop and Illustrator

Every tool that changes a document returns:

| Field | Use |
|---|---|
| `session_id` | Names the owned copy. A later tool call, or a later agent, continues the same copy by passing it. A session belongs to one application process and one workspace. |
| `operation_id` | Names this operation, for recovery and for the record. |
| `document` | The snapshot after the change: size, units, colour profile, bit depth, `layers` and `paths`. |
| `document.changed_layers` | The layers this operation created or changed, each with `path`, `name` and `native_id`. |
| `copy` | `path`, `sha256` and `bytes` of the saved copy. |
| `source_sha256` | The hash of the source file (or canvas recipe) the copy came from. |
| `retry_safe` | Always `false`. |

To pick up where a call stopped, call `photoshop_inspect` or `illustrator_inspect` with the `session_id`. It returns the current snapshot and the
stored session record.

### Finding the new layer

Layer paths shift when layers are added, so find a layer by `changed_layers`, not by guessing its index. After the
Photoshop generative tools:

- `photoshop_generative_fill` puts the generated layer in `changed_layers`. Pass its `path` and `name` as
  `layer_path` and `expected_name` to later edits. A generated layer already has its own mask, so
  `photoshop_remove_background` rejects it.
- `photoshop_remove_background` lists the masked layer. Its `has_user_mask` is then `true` in the snapshot.
- `photoshop_generative_upscale` lists the new top layer named `Generative Upscale`. The document is now twice its
  earlier width and height; read the new size from `document.width` and `document.height`.

`native_id` is Photoshop's own layer id, also shown on each layer in the snapshot as `id`. Illustrator reports
`null`.

### Exports

`photoshop_export` and `illustrator_export` write the file and `<destination>.receipt.json`. A pipeline takes the receipt, reads
`artifact.path` and `artifact.sha256`, hashes the file itself and continues only if they match. `usage` `color`
gives an sRGB image; `data` gives values with no colour interpretation, for maps and masks. `native_export_sha256`
is the hash of what the application wrote before the server adjusted colour chunks. See [Exports and
receipts](exports-and-receipts.md).

The PSD or AI copy itself is a handoff too: its `copy.sha256` pins it, and the layers stay editable.

## Review movies

`media_review_movie` takes a media receipt and writes `review.mov` or `review.mp4` and `movie.json`. Pass on `path` and
`sha256` of the movie and `media_receipt_sha256` to show which frames it was made from. It is for review, not for
final delivery: see [Review movies](review-movies.md).

## Animate

`animate_export_frames` returns the frames and writes `media.json`:

- `frames` lists each PNG with `path`, `sha256`, `width`, `height` and `rect`.
- `media` is the validated receipt; its `fps` is an exact numerator and denominator.
- `source_snapshot` pins the FLA the frames came from. `sources` in `media.json` also names the original PNG.

A pipeline reads `media.json`, checks each frame hash and uses the timeline order of the frames. The same receipt feeds
`media_review_movie`. The FLA copy is still open and editable in Animate until you close it with `animate_close_copy`.

`before_edit_snapshot` from an edit keeps the document as it was before that edit.

## Dimension

`dimension_render_inspect` returns the receipt, also written as `receipt.json` next to the PNG. Take `handoff_output`
and `handoff_sha256`. The handoff file is always an 8-bit RGBA PNG: an RGB render is converted to `render-rgba.png`
with opaque alpha, and `handoff_converted` says so. A render that is not 8-bit RGB or RGBA returns `unsupported` and
has no handoff file. See [Renders](../dimension/renders.md).

## Before you continue

- Check the hash of every file against the result, not just its name.
- Treat `visual_acceptance` `unreviewed` as an open question until someone has looked.
- If a result says `status` `pending`, or an error reports an unknown outcome, the work is not finished. See [Recovery
  and unknown outcomes](recovery.md).
