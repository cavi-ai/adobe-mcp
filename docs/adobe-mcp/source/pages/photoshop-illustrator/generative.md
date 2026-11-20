# Generative tools

Three Photoshop tools use Photoshop's generative features: `adobe_generative_fill`, `adobe_remove_background` and
`adobe_generative_upscale`. They work on an owned PSD copy, are refused for Illustrator before anything is sent to
the application, and require an RGB document.

## Account, credits and the first-use notice

Generative Fill and Generative Upscale use the signed-in Creative Cloud account, send pixels to Adobe and spend
generative credits. The server never signs in.

Photoshop shows a one-time Generative Credits notice. Acknowledge it in the application before the first call.
Otherwise the call times out after 120 seconds and reports an unknown outcome. Resolve that with
[Recovery and unknown outcomes](../guides/recovery.md) before you change the copy again.

## `adobe_generative_fill`

| Parameter | Meaning |
|---|---|
| `application`* | `photoshop` |
| `session_id`* | The owned copy |
| `prompt`* | Up to 1000 printable characters. An empty prompt is accepted; control characters are not. |
| `bounds` | `[x, y, width, height]` in pixels: `x` and `y` from 0, `width` and `height` from 1, each up to 30000, inside the document. Without `bounds` the fill covers the whole canvas. |

Photoshop generates a new layer. The call fails unless exactly one layer was added. The layer is listed in
`changed_layers`.

## `adobe_remove_background`

| Parameter | Meaning |
|---|---|
| `application`* | `photoshop` |
| `session_id`* | The owned copy |
| `layer_path`*, `expected_name`* | The one layer to mask |

The layer must be a pixel layer or a smart object, not the background layer, not locked and without a mask. A
generated layer arrives with its own mask, so Remove Background rejects it. Photoshop adds a layer mask that keeps
the detected subject. The masked layer is listed in `changed_layers`.

## `adobe_generative_upscale`

| Parameter | Meaning |
|---|---|
| `application`* | `photoshop` |
| `session_id`* | The owned copy |

The document must be at most 1440 px on each side. The document becomes twice as wide and twice as high, and the
upscaled image is added as the top layer, named `Generative Upscale`. The new layer is listed in `changed_layers`.

## Results

Each tool returns the mutation result: `session_id`, `operation_id`, the `document` snapshot with `changed_layers`
added, the `copy` artifact, `source_sha256` and `retry_safe` `false`. A repeated call spends credits again and
adds another layer.

The tool descriptions mark Generative Fill and Generative Upscale as open-world, because they reach a service
outside the machine.

## Limits and refusals

| Condition | Result |
|---|---|
| `prompt` over 1000 characters or with control characters | Refused before dispatch |
| `bounds` malformed, negative origin, zero size or outside the document | Refused |
| Document over 1440 px on a side | Upscale refused |
| Layer is the background, text, a group, locked or already masked | Remove Background refused |
| Application is Illustrator | Refused before dispatch |
| Photoshop adds no layer or no mask | Reported as an unknown outcome |
| 120 seconds pass | Unknown outcome |
