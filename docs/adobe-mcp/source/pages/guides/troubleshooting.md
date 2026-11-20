# Troubleshooting

Messages are shown as the servers report them.

## The application is not running

Photoshop, Illustrator and Animate must be open before a document tool is called. The servers do not launch them.
Messages: `App is not running; open it manually and resolve any prompts first` (Photoshop and Illustrator) and
`APP_NOT_RUNNING` (Animate). For Dimension, call `dimension_launch`; `dimension_render_start` reports `APP_NOT_RUNNING`
otherwise.

## More than one process

`Multiple app processes found; select a single instance before authoring` (Photoshop, Illustrator), `APP_AMBIGUOUS`
(Animate) and `APPLICATION_AMBIGUOUS` (Dimension launch). Quit the extra instances.

## The wrong application

`APP_IDENTITY_MISMATCH`: the path in `CAVI_PHOTOSHOP_APP`, `CAVI_ILLUSTRATOR_APP` or `CAVI_ANIMATE_APP` is another
application. An override is final and is never replaced by another install. Remove it or fix the path. Animate
needs Animate 2024.

## A call waits and then reports an unknown outcome

A dialog is probably open in the application. Common ones are a sign-in prompt, a licence prompt, Animate's script
chooser and, for the Photoshop generative tools, the one-time Generative Credits notice. Clear it in the application,
then follow [Recovery and unknown outcomes](recovery.md). A prompt seen by the Animate or Dimension helper stops the
call with `PROMPT_BLOCKED` or `AUTHENTICATION_BOUNDARY`.

## Refusals before dispatch

| Message | Cause |
|---|---|
| `Another Adobe authoring operation is active` | A command is running for that application. Wait for it. |
| `An earlier Adobe command has no resolved outcome` | Resolve the pending operation first. |
| `Source changed since this copy was created` / `Working copy changed outside this session` | The file moved on after the session began. Open a new copy. |
| `Session app process changed; ownership is no longer valid` | The application restarted. Open a new copy. |
| `Destination already exists` / `Output must not already exist` | Outputs must be new paths. |
| `Layer name changed; inspect the exact path again` | The layer at that path is not the one you named. Inspect and use the current `path` and `name`. |
| `Exact PostScript font is unavailable; use fonts discovery` | Use a `postscript_name` from `photoshop_fonts` or `illustrator_fonts`. |
| `Rectangle exceeds document bounds` / `Fill bounds exceed document bounds` | Shrink `bounds` to the document. |
| `Layer already has a mask` | One mask per layer. A generated layer already has one. |
| `Remove Background requires a pixel or smart object layer that is not the background` | Pick another layer. |
| `Generative Upscale requires at most 1440 px per side` | Resize first with `photoshop_resize_image`. |
| `Mask requires one exact closed path` | Name exactly one closed path. |

## The native helper

`NATIVE_HELPER_UNAVAILABLE`: the Swift compiler is missing; run `xcode-select --install`. `NATIVE_HELPER_BUILD_FAILED`:
compiling failed or took over 300 seconds. `NATIVE_HELPER_UNSAFE`: the cached build under `.animate-authoring/native/`
or `.dimension-authoring/native/` failed its check; the message names the folder to remove so it is rebuilt.
`ACCESSIBILITY_UNAVAILABLE` (Animate) and `PERMISSION_UNAVAILABLE` (Dimension): grant the access to the MCP host in
macOS settings yourself; the server does not request it.

## Review movies

`Install the free FFmpeg and ffprobe executables`: install both. `Review frames must share dimensions`: the receipt's
frames differ in size.

## Dimension renders

`JOB_ACTIVE` or `JOB_BUSY`: inspect the job named in the message with `dimension_render_inspect`. `unsupported`: Dimension
wrote a PNG that is not 8-bit RGB or RGBA.
