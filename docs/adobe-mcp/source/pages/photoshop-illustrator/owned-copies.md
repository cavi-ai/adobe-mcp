# Owned copies

The authoring server never edits the file you give it. `adobe_open_copy` and `adobe_create_canvas` make a new
document that the session owns, and every later edit targets that document only.

## How a copy is made

`adobe_open_copy` copies the source into a private input copy under `.adobe-authoring/`, hashes it, reserves the new
destination file, opens the input copy in the application and saves it as the destination (a layered PSD in
Photoshop, an AI file in Illustrator). The source file is not opened or saved. Unsaved changes in a document you
already have open are not captured.

`adobe_create_canvas` writes a retained recipe (`canvas_recipe`) that records the width, height, resolution (72),
mode (RGB), bit depth, transparent background and sRGB profile, and creates the document from it. The recipe takes
the place of a source file: the result has `source_kind` `canvas_recipe` and `source_sha256_kind`
`canvas_recipe_json`.

Both tools refuse a destination that exists. The result gives:

| Field | Meaning |
|---|---|
| `session_id` | 32 hexadecimal characters. Name it in every later call. |
| `operation_id` | This operation. |
| `document` | The snapshot of the open document. |
| `copy` | `path`, `sha256` and `bytes` of the saved copy. |
| `source_sha256` | The hash of the source, or of the canvas recipe. |
| `retry_safe` | `false` |

## What is checked before each change

Before and after a native command the server checks that:

- the application process is the one that opened the copy (restarting the application ends ownership);
- the source still has its recorded hash;
- the copy on disk still has the hash the last operation recorded, so an outside edit is refused;
- the document in the application still matches the last snapshot (inside the application, an edit you make by hand
  stops the next operation).

Any of these failing before dispatch refuses the call. A source that changes during a command, or a process that
changes during it, is reported as an unknown outcome.

## Closing

`adobe_close_copy` closes the owned document. Every operation saves the copy before it returns, so the file on disk
already holds the result. After closing, the session accepts no further edits.

## Records

Session records are `.adobe-authoring/<session_id>.json`. The input copies and canvas recipes are kept beside them.
They are private to the workspace; do not edit or share them. An unresolved operation is tracked in a journal in the
system temporary directory, covering the application across workspaces; see [Recovery and unknown
outcomes](../guides/recovery.md).
