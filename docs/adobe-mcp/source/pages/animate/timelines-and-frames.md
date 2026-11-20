# Timelines and frames

## Editing a timeline

`animate_edit_timeline` takes `session_id` and `batch`, an object with `edits`: 1 to 128 edits applied in order. The
batch is checked first; Animate then saves a before-edit snapshot, applies the edits and saves the document. Frame
numbers are zero-based. Every edit names a `layer` (1 to 128 characters). Unknown fields are refused.

| `action` | Fields | What it does |
|---|---|---|
| `add_layer` | `layer` | Adds a new normal layer. Fails if a layer has that name. |
| `duration` | `layer`, `count` (1 to 1000) | Extends the layer to `count` frames. Fails if it would discard authored frames. |
| `keyframe` | `layer`, `frame` (0 to 999), `blank` (default `false`) | Converts the frame to a keyframe, or to a blank keyframe. |
| `transform` | `layer`, `frame`, `element` (0 to 10000), `matrix` | Sets the element's matrix: six numbers `a, b, c, d, tx, ty`, each from -100000 to 100000. |
| `symbol` | `layer`, `frame`, `element`, `name`, `symbol_type` | Converts the element to a library symbol. `symbol_type` is `graphic` (default), `movie clip` or `button`. Fails if the name exists. |
| `place_symbol` | `layer`, `frame`, `name`, `x`, `y` | Places an existing library symbol at `x`, `y`. |
| `tween` | `layer`, `frame`, `end`, `easing` (-100 to 100, default 0) | Makes a classic motion tween from `frame` to `end`. `end` must be after `frame`. |

Rules the driver applies:

- The layer name must match exactly one layer. It must be a normal, unlocked layer.
- A frame edit needs the layer to already extend to that frame; use `duration` first.
- `transform`, `symbol`, `place_symbol` and `tween` need an authored keyframe at `frame`. Create it with `keyframe`.
- `tween` needs keyframes at both ends, each holding one instance of the same library symbol.
- A failed edit stops the batch before the document is saved. The call reports an unknown outcome, and the
  before-edit snapshot holds the document as it was before the call. Inspect Animate before you continue.

A typical sequence: `add_layer`, `duration`, `keyframe` at the start and end, `symbol` on the artwork, `transform` at
the end keyframe, `tween` between them.

## Exporting frames

`animate_export_frames` takes `session_id` and a new `directory` in an existing folder. It exports one PNG per
timeline frame, in timeline order, after saving a snapshot of the document.

Limits: the document has 1 to 1000 frames, a frame rate of 1 to 120, and a length of at most 300 seconds.

The directory holds:

| File | Content |
|---|---|
| `frame_0001.png`, `frame_0002.png`, ... | One single-frame RGBA PNG per frame. |
| `document-source.fla` | The document as exported. |
| `media.json` | The media receipt. |

The media receipt records the document name, `fps` as a numerator and denominator, `loop` `false`, straight alpha,
a top-left coordinate origin, and every frame with its `name` (`Frame0001`, ...), `path`, `sha256`, `width`, `height`
and `rect`. Its `sources` list the exported FLA snapshot and the original source file, each with a hash. Paths are
relative to the workspace.

The result has `source_snapshot`, `media` (the validated receipt), `frames` and `visual_acceptance` `unreviewed`.
Pass the path of `media.json` to `media_review_movie` to make a [review movie](../guides/review-movies.md).
