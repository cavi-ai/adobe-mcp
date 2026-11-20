# Animate tools

The Animate server (`cavi-animate-mcp`) has 9 tools. It works on Adobe Animate 2024 (bundle identifier
`com.adobe.Adobe-Animate-2024.application`) and only on FLA copies it owns. Animate must already be open. A
parameter marked `*` is required.

Commands go to Animate as a fixed JSFL script and typed data; no caller-supplied script is run. A command waits up to
60 seconds for Animate's receipt. Animate may show a chooser while it runs the script; a helper handles that one
chooser and stops at any sign-in, licence or permission prompt (`PROMPT_BLOCKED`). The helper is compiled on first use and needs existing
Accessibility access.

| Tool | Parameters | Result |
|---|---|---|
| `animate_status` | none | `application`, `version`, `running`, `process`, `launches_application` `false`, `authenticates` `false`. Does not launch Animate. |
| `animate_inspect` | none | `native` with the open documents (up to 128), `pending_operation` and `launches_application` `false`. |
| `animate_open_copy` | `source`*, `destination`* | Copies a workspace `.fla` to a new `.fla` and opens that copy. |
| `animate_create_from_png` | `source`*, `destination`* | Creates a timeline document from one single-frame 8-bit RGBA PNG and saves it as a new `.fla`. |
| `animate_set_stage_size_from_source` | `session_id`* | Sets the stage to the pixel size of the session's source PNG (1 to 2880 each side). |
| `animate_edit_timeline` | `session_id`*, `batch`* | Applies `batch.edits`. See [Timelines and frames](timelines-and-frames.md). |
| `animate_export_frames` | `session_id`*, `directory`* | Exports every frame as a PNG with a media receipt. |
| `animate_close_copy` | `session_id`* | Saves and closes the owned copy. |
| `animate_operation_control` | `session_id`*, `operation_id`*, `action`, `expected_copy_sha256`, `expected_native_sha256` | Recovery. `action` is `inspect` (default), `adopt_current`, `cancel_unsubmitted` or `abandon_after_exit`. |

## Results

Every tool that changes Animate returns `operation_id`, `session` (the session id; pass it as `session_id` in later
calls), `document` and `retry_safe` `false`. The `document` is what Animate reports for the file, including its
`path_uri`. Extra fields:

| Tool | Added fields |
|---|---|
| `animate_open_copy` | `source_sha256`, `document_sha256` |
| `animate_create_from_png` | `source_sha256`, `document_sha256` |
| `animate_set_stage_size_from_source` | `source_sha256`, `document_sha256`, `before_edit_snapshot` |
| `animate_edit_timeline` | `before_edit_snapshot`, `document_sha256` |
| `animate_export_frames` | `source_snapshot`, `media`, `frames`, `visual_acceptance` `unreviewed` |
| `animate_close_copy` | `document` is `{"path_uri": ..., "closed": true}` |

A `before_edit_snapshot` or `source_snapshot` is `path`, `sha256` and `size_bytes` of an FLA saved before the change
(or of the exported document). A before-edit snapshot is saved beside the document as `<name>-before-edit-<id>.fla`,
or `<name>-before-stage-size.fla` for the stage size. The export snapshot is `document-source.fla` in the export
directory. A snapshot over 250 MiB is an unknown outcome.

## Rules

- `source` and `destination` are workspace paths. A destination must be new and its folder must exist.
- The session's source file is hashed. If the source or the FLA copy changes outside the session, the session is
  refused.
- The exact owned FLA must be open once in Animate for stage size and export.
- One mutation at a time. A mutation that did not report back blocks the next until you resolve it; see [Recovery and
  unknown outcomes](../guides/recovery.md).
- `animate_create_from_png` saves a private staged FLA first, links it to the destination, then opens the destination.
