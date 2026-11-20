# Quickstart

This walks one PNG in the workspace to an edited Photoshop copy and an exported PNG. Paths are relative to
`CAVI_ADOBE_WORKSPACE`. Photoshop must already be open.

1. Check the application. `adobe_status` with `application` `photoshop` reports `running`, the `version` and the
   `process`. It does not launch Photoshop.

2. Open an owned copy. `adobe_open_copy` with `application` `photoshop`, `source` `art/hero.png` and
   `destination` `work/hero.psd`. The destination must not exist and its folder must. The result has a `session_id`,
   an `operation_id`, the `document` snapshot with its `layers`, the `copy` artifact and the `source_sha256`.

3. Look at the layers. `adobe_inspect` with the `session_id` returns the same snapshot. Each layer has a `path`, a
   list of zero-based indices from the document root, and a `name`.

4. Edit by path and name. Take a layer's `path` and `name` from the snapshot. `adobe_edit_layers` with `edits`
   `[{"path": [0], "expected_name": "<name shown>", "name": "Hero"}]` renames layer `[0]` only if it still has that
   name. The result lists the touched layer in `changed_layers`.

5. Add editable text. `adobe_fonts` lists installed fonts. Pass one `postscript_name` to `adobe_add_text` with `name`,
   `text`, `position`, `size` and `rgb`.

6. Export. `adobe_export` with `session_id`, `destination` `out/hero.png`. Photoshop writes a PNG next to a
   `out/hero.png.receipt.json` file with the hashes of the export.

7. Close. `adobe_close_copy` closes the owned document.

If any step reports an unknown outcome, stop and read [Recovery and unknown
outcomes](../guides/recovery.md) before you try again.

Next: [Photoshop and Illustrator tools](../photoshop-illustrator/tools.md), [Generative
tools](../photoshop-illustrator/generative.md) and [Exports and receipts](../photoshop-illustrator/exports-and-receipts.md).
