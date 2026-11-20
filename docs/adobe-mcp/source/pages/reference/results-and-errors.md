# Results and errors

Tools return structured content that matches the tool's output schema. A failed call is a tool error. The error
shape is described in the [MCP Core
contract](https://github.com/cavi-ai/mcp-core/blob/main/docs/mcp-core/source/pages/reference/results-and-errors.md).
This page lists what these servers return.

## Photoshop and Illustrator results

| Tool | Result |
|---|---|
| `photoshop_status`, `illustrator_status` | `app`, `application`, `version`, `running`, `launches_application` `false`, `authenticates` `false` |
| `photoshop_inspect`, `photoshop_fonts`, `illustrator_inspect`, `illustrator_fonts` | `runtime`, `pending_operation`, plus `version` and `documents`, or `fonts`; with a `session_id`, the owned copy's snapshot and `session`. `photoshop_fonts` also returns `font_list` and a `source` and `file` for each font ([Photoshop fonts](../photoshop/fonts.md)) |
| `photoshop_text_fonts` | `runtime`, `pending_operation`, `layers` and `missing_fonts` |
| every tool that changes a document | the mutation result below |
| `photoshop_operation_control`, `illustrator_operation_control` | `state`, `operation_id`, `session_id`, `retry_safe`, `completion_available`, `can_reconcile`, `copy_sha256`, `native_sha256`, `document` |

`media_review_movie` is served by the Animate server and returns `path`, `sha256`, `codec`, `stream`, `execution`,
`visual_acceptance` and `launches_applications`.

The mutation result:

| Field | Meaning |
|---|---|
| `session_id` | The owned copy |
| `operation_id` | This operation |
| `document` | The snapshot after the change. See [Photoshop tools](../photoshop/tools.md) and [Illustrator tools](../illustrator/tools.md). |
| `document.changed_layers` | `path`, `name` and `native_id` of every layer the operation created or changed. Present for the edit, add, place, mask and generative tools. An unfilled Photoshop path from `photoshop_add_path` adds no layer, so the list is empty. |
| `copy` | `path`, `sha256` and `bytes` of the saved copy |
| `source_sha256` | The hash of the source file or canvas recipe |
| `retry_safe` | `false` |

Additions: `photoshop_create_canvas` returns `source_kind`, `source_sha256_kind` and `canvas_recipe`;
`photoshop_place_image` and `illustrator_place_image` return `placement_source`; `photoshop_export` and
`illustrator_export` return `artifact`, `native_export_sha256`, `visual_acceptance` and `receipt`. The close tools
return `document` as `{"closed": true, "path": ...}`.

The three Photoshop generative tools return the plain mutation result. `photoshop_generative_fill` lists the
generated layer, `photoshop_remove_background` the masked layer and `photoshop_generative_upscale` the layer named
`Generative Upscale`.

## Animate and Dimension results

See [Animate tools](../animate/tools.md), [Timelines and frames](../animate/timelines-and-frames.md) and
[Renders](../dimension/renders.md). Animate results use `session` for the session id. Both servers return
`retry_safe` `false` and `launches_application` `false` for the read tools.

## Outcomes

A failure has one of two outcomes.

- **Rejected.** The request was refused before a native command was dispatched. Nothing changed. Fix the request and
  send it again. Examples: an invalid argument, a path outside the workspace, an existing output, a changed layer
  name, Remove Background on a masked layer, Upscale over 1440 px, or a native preflight check that failed.
- **Unknown.** A native command may have run. The record stays and later changes are refused until it is resolved. See
  [Recovery and unknown outcomes](../guides/recovery.md). Examples: a timeout, a Generative Credits notice that was
  not acknowledged, an application that changed during the command, or a receipt that did not match.

Every result that reports an operation has `retry_safe` `false`.

## Error codes

Photoshop and Illustrator errors carry the message of the failed check. The Animate and Dimension servers and the
native helpers also use these codes:

| Code | When |
|---|---|
| `APP_IDENTITY_MISMATCH` | The application at the path is not the expected one, or its process changed. |
| `APP_NOT_RUNNING` | Animate, or Dimension for a render, is not running. |
| `APP_AMBIGUOUS`, `APPLICATION_AMBIGUOUS` | More than one process of the application. |
| `PLATFORM_UNSUPPORTED` | The host is not macOS. |
| `NATIVE_HELPER_UNAVAILABLE`, `NATIVE_HELPER_BUILD_FAILED`, `NATIVE_HELPER_UNSAFE` | The Swift helper is missing its compiler, failed to build, or its cache failed its check. |
| `STATE_UNSAFE` | A state directory is a symbolic link. |
| `PROMPT_BLOCKED`, `AUTHENTICATION_BOUNDARY` | A sign-in, licence or permission prompt is visible. No input was sent to it. |
| `ACCESSIBILITY_UNAVAILABLE`, `PERMISSION_UNAVAILABLE` | Existing access is missing. No request was made. |
| `CANCELLED` | The request was cancelled before native dispatch. |
| `DOCUMENT_IDENTITY_MISMATCH` | The exact owned FLA is not open once in Animate. |
| `VOLUME_IDENTITY_UNAVAILABLE` | The macOS root volume name could not be read. |
| `JOB_BUSY`, `JOB_ACTIVE` | A Dimension render is running or unresolved. |
| `INVALID_ARGUMENT`, `INVALID_PATH` | A request or path the helper refused. |
| `OUTCOME_UNKNOWN` | A dispatched command did not report a result. |

The Dimension helper has further codes of its own, such as `WINDOW_UNAVAILABLE`, `FOCUS_CHANGED`,
`CHOOSER_UNAVAILABLE`, `DIRECTORY_NOT_OBSERVED`, `STALE_PROCESS`, `CAPTURE_FAILED` and `DESTINATION_EXISTS`.
