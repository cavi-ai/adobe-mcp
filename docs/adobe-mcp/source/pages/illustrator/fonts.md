# Illustrator fonts

`illustrator_fonts` lists the fonts and `illustrator_set_text_font` changes the font of one text frame.
`illustrator_add_text` also checks the font it applies.

No tool activates, deactivates or downloads a font. Adobe Fonts are activated in Creative Cloud, in the Creative Cloud
desktop app or on fonts.adobe.com. Activate the font there, then call `illustrator_fonts` again. Illustrator must be running.

## `illustrator_fonts`

No parameters. `fonts` lists `name`, `family`, `style` and `postscript_name` for every installed font. Illustrator's
scripting interface gives neither a font file path nor a flag for a missing font, so this tool reports no `source`
and cannot tell Adobe Fonts from other fonts. `postscript_name` is the font's name as Illustrator reports it.

## `illustrator_set_text_font`

| Parameter | Meaning |
|---|---|
| `session_id`* | The owned copy |
| `layer_path`*, `expected_name`* | The layer that holds the text frame, as in [Illustrator tools](tools.md) |
| `frame_index`* | Zero-based index (0 to 10000) among the layer's direct text frames |
| `font`* | An exact name from `illustrator_fonts`, 1 to 128 printable characters |

The font is applied to the whole frame. The result is the mutation result, with the layer in `changed_layers`.

| Condition | Result |
|---|---|
| Invalid `session_id`, `layer_path`, `expected_name`, `frame_index` or `font` | Refused before dispatch |
| The layer is locked, the frame does not exist or the frame is locked | Refused |
| `font` matches no installed font name exactly | Refused: `Exact PostScript font is unavailable; use fonts discovery` |
| After the assignment the frame reports another font | Unknown outcome, because the document may already have changed. Resolve it with [Recovery and unknown outcomes](../guides/recovery.md). |

## `illustrator_add_text`

After the frame is created, its font must read back as the requested one. A frame that keeps another font is an
unknown outcome, as for `illustrator_set_text_font`.
