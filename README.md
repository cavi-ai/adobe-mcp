# Cavi Adobe MCP

Local MCP servers for Adobe Photoshop, Illustrator, After Effects, Animate, Character
Animator and Dimension. Each server edits owned copies of documents inside one
workspace, records every native operation before it runs, and reports an uncertain
outcome as unknown instead of retrying it.

| Command | Server name | Applications |
|---|---|---|
| `cavi-adobe-authoring-mcp` | `cavi-adobe-authoring` | Photoshop, Illustrator, After Effects (`aerender`), FFmpeg review movies |
| `cavi-animate-mcp` | `cavi-animate-authoring` | Animate |
| `cavi-character-animator-mcp` | `cavi-character-animator-mcp` | Character Animator |
| `cavi-dimension-mcp` | `cavi-adobe-dimension` | Dimension |

Startup and tool discovery never launch an application. A tool reaches an application
only when it is called, and only an application that is already installed and licensed.

## Requirements

- macOS, Python 3.11+ and [uv](https://docs.astral.sh/uv/).
- The Adobe applications you use, installed and signed in. No license or account is
  supplied by these servers.
- Automation access for the MCP host to control the application (Photoshop,
  Illustrator, Animate). The servers never request permissions; they report a denial.
- Animate and Character Animator also use a small native helper compiled on first use,
  which needs the Swift command-line compiler (`xcode-select --install`) and existing
  Accessibility access. Character Animator capture also needs Screen Recording access.
- `ffmpeg` on `PATH` for review movies.

## Install

```sh
uv tool install cavi-adobe-mcp
```

Register the servers you need in your MCP client and set the workspace:

```json
{
  "mcpServers": {
    "adobe-authoring": {
      "command": "cavi-adobe-authoring-mcp",
      "env": {"CAVI_ADOBE_WORKSPACE": "/Users/me/Art"}
    }
  }
}
```

## Configuration

| Variable | Meaning |
|---|---|
| `CAVI_ADOBE_WORKSPACE` | Absolute directory every tool path is relative to. Required. |
| `CAVI_ADOBE_DATA` | Private server state: native helper builds and Character Animator journals. Defaults to `~/.local/share/cavi-adobe-mcp`. |
| `CAVI_CHARACTER_ANIMATOR_APP` | The Character Animator application, when more than one is installed. |

Ownership records live inside the workspace (`.adobe-authoring/`, `.animate-authoring/`);
keep them private.

## Tools

- Authoring: `adobe_status`, `adobe_inspect`, `adobe_open_copy`, `adobe_create_canvas`,
  `adobe_edit_layers`, `adobe_resize_image`, `adobe_add_group`, `adobe_place_image`, `adobe_add_rectangle`,
  `adobe_add_path`, `adobe_add_text`, `adobe_apply_mask`, `adobe_fonts`, `adobe_export`,
  `adobe_close_copy`, `adobe_operation_control`, `after_effects_render`,
  `media_review_movie`.
- Animate: `animate_status`, `animate_inspect`, `animate_open_copy`,
  `animate_create_from_png`, `animate_set_stage_size_from_source`, `animate_edit_timeline`,
  `animate_export_frames`, `animate_close_copy`, `animate_operation_control`.
- Character Animator: `character_status`, `session_control`, `project_select`,
  `project_save_copy`, `inspect`, `capture`, `scene_action`, `scene_export`,
  `import_media`, `export_inspect`, `export_ingest`, `operation_control`.
- Dimension: `dimension_status`, `dimension_launch`, `dimension_inspect`,
  `dimension_render_start`, `dimension_render_inspect`.

For a closed Character Animator project, use `project_prepare_copy` to copy its saved
`.chproj` project and `Ch Data` tree into a new workspace destination. Pass the returned
`prepared_copy_id` to `project_open_prepared_copy`, inspect the actual
opened project root, then use `session_control(action="claim", prepared_copy_id=...)` to
establish ownership before editing. Open and claim operate on that exact prepared copy.

Every tool error returns one structured result (`code`, `outcome`, `next_action`) from
[`cavi-mcp-core`](https://github.com/cavi-ai/mcp-core). An `unknown` outcome means the
application may have changed: inspect it, or resolve the recorded operation with the
server's `operation_control` tool, before trying again.

When a pending Character Animator chooser is behind the app, the server activates the
app and rechecks the recorded process, project, and exact chooser before continuing.
For an unknown initial Save As whose app process has exited,
`operation_control(action="reconcile_copy_after_exit")` can record that the destination
is absent and no copy was verified; it does not report a successful save or a user cancellation.
For an unknown operation on a first-hand owned project, `operation_control(action="abandon_after_exit")`
archives the operation after two stable observations confirm that its original process and all Character Animator
instances have exited. The operation remains recorded as unknown; project and media files are preserved.

If Character Animator has exited and no operation is pending, release the original lease with
`session_control(action="release", lease_id=...)`. The server verifies stable process absence
and the unchanged owned project folder before removing the lease.

Interrupted native chooser calls retain bounded attempt diagnostics under `CAVI_ADOBE_DATA`.
These diagnostics do not establish completion or authorize a retry.

## Development

```sh
uv sync --locked --dev
uv run --locked python -m unittest discover -s tests
uv run --locked ruff check .
```

Tests that drive the real applications run only when their `CAVI_*_NATIVE` variable is
set; the rest use recorded responses and never launch an application.

## License

MIT. Adobe, Photoshop, Illustrator, After Effects, Animate, Character Animator and
Dimension are trademarks of Adobe Inc.; this project is not affiliated with Adobe.
