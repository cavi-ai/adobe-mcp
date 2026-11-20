# Renders

A render is two calls: `dimension_render_start` submits it, `dimension_render_inspect` verifies it. Submission is not
completion.

## Start

`dimension_render_start` takes `scene`, a saved `.dn` file in the workspace, and `directory`, a new directory in an
existing folder. It:

1. refuses if another render in this workspace has no result yet, or if Dimension is not running;
2. creates the directory, copies the scene to `render.dn` there and hashes both files;
3. has the helper open that copy and run the render through Dimension's visible controls, choosing the directory
   with the folder chooser.

The source scene is never opened. The result:

| Field | Meaning |
|---|---|
| `operation_id` | The render job. Pass it to `dimension_render_inspect`. |
| `status` | `render_requested` |
| `output` | `<directory>/render.png`, the file Dimension is to write |
| `directory`, `owned_copy` | The new directory and `render.dn` |
| `captures` | Paths of window captures the helper saved (at most 8) |
| `source_identity` | `opened_owned_copy` |
| `input_sha256`, `owned_copy_sha256` | Hashes of the scene and its copy |
| `process_identity` | The Dimension process the render was sent to |
| `retry_safe` | `false` |

The start is refused or reported unknown if the process changes, or if the scene or its copy changes, while the
request is sent.

## Inspect

`dimension_render_inspect` takes the `operation_id` and returns `status`:

| `status` | Meaning |
|---|---|
| `pending` | `render.png` does not exist yet. Call again later. |
| `complete` | The PNG was verified. The result is the receipt. |
| `unsupported` | The PNG exists but is not an 8-bit RGB or RGBA PNG. The result is the receipt, with `reason`. |
| `rejected` | The start was rejected. The result has the `error`. |

The PNG is checked for validity, a size of at most 512 MiB and at most 120,000,000 pixels, and the scene, its copy
and the PNG must not change during verification. A failure is an unknown outcome that keeps the job open.

## The receipt

On `complete` and `unsupported` the receipt is returned and written as `receipt.json` next to `render.png`. It is
never overwritten. A `complete` receipt has:

| Field | Meaning |
|---|---|
| `output`, `sha256`, `bytes`, `width`, `height`, `mode`, `bit_depth` | The rendered PNG |
| `handoff_output`, `handoff_sha256`, `handoff_converted` | The file to hand on. For an RGBA render it is `render.png` itself. For an RGB render it is `render-rgba.png`, with alpha 255 and the same RGB pixels and ICC profile, and `handoff_converted` is `true`. |
| `input`, `input_sha256`, `owned_copy`, `owned_copy_sha256` | The scene and its copy |
| `source_identity`, `captures`, `process_identity`, `verified_at` | How the render was made |

Repeated inspection of a finished job returns the same receipt.

Take `handoff_output` and `handoff_sha256` from the receipt to continue in another tool. See
[Handoffs](../guides/handoffs.md).
