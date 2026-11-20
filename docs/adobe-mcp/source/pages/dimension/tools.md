# Dimension tools

The Dimension server (`cavi-dimension-mcp`) has 5 tools. It drives Adobe Dimension at
`/Applications/Adobe Dimension/Adobe Dimension.app` only (bundle identifier `com.adobe.dimension`). It controls the
application through its visible windows with a Swift helper that is compiled on first use. The helper needs existing
Accessibility and Screen Recording access and permission to post input events, and it never requests them.

| Tool | Parameters | Result |
|---|---|---|
| `dimension_status` | none | `native` (the installed application and process state) and `launches_application` `false`. |
| `dimension_launch` | none | Opens the fixed installed application once, or reuses the running process. Returns `status` (`launched` or `already_running`), `process_identity`, `bundle_id`, `bundle_path` and `launches_application`. |
| `dimension_inspect` | none | `native` (visible Dimension windows and accessibility controls) and `launches_application` `false`. Performs no action in the application. |
| `dimension_render_start` | `scene`*, `directory`* | Submits a render of a saved `.dn` scene. See [Renders](renders.md). |
| `dimension_render_inspect` | `operation_id`* | Verifies the render job. See [Renders](renders.md). |

A parameter marked `*` is required. `scene` is a workspace `.dn` file. `directory` is a new directory in an existing
folder.

## Rules

- `dimension_launch` is the only tool that starts Dimension. A render needs Dimension to be running; otherwise
  `dimension_render_start` is refused with `APP_NOT_RUNNING`.
- A visible sign-in, licence or permission prompt stops the call (`AUTHENTICATION_BOUNDARY` or `PROMPT_BLOCKED`).
  Nothing is clicked or typed into it.
- Only one render job is active in a workspace. A second start is refused (`JOB_BUSY` or `JOB_ACTIVE`) until the
  first is inspected to a result.
- `dimension_launch` is refused with `APPLICATION_AMBIGUOUS` when several Dimension processes are running.
- The helper call limit is 30 seconds, and 60 seconds for starting a render. A timeout is an unknown outcome, and the
  render is checked with `dimension_render_inspect`.
- State is kept in `.dimension-authoring/` in the workspace.
