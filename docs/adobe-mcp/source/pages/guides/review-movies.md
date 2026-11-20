# Review movies

`media_review_movie` turns a set of pinned frames into one movie for review. It runs the free local `ffmpeg` and
`ffprobe` programs. It starts no Adobe application. It is served by the Animate server (`cavi-animate-mcp`).

| Parameter | Meaning |
|---|---|
| `receipt`* | The workspace path of a media receipt, such as the `media.json` that `animate_export_frames` writes |
| `directory`* | A new directory for the output |
| `codec` | `prores_alpha` (default) or `h264_black` |

## Codecs

| `codec` | File | Format |
|---|---|---|
| `prores_alpha` | `review.mov` | ProRes 4444 with alpha (`yuva444p10le`). Transparency is kept. |
| `h264_black` | `review.mp4` | H.264 `yuv420p`. Each frame is composited on black first, and the size is padded to even dimensions. Transparency is lost. |

## What happens

1. The receipt is validated: every frame file must still have its recorded hash.
2. All frames must share one size, and the expanded frames must not exceed 256 MiB of RGBA pixels.
3. Each frame is cropped to its `rect` and written to `frame_000000.png`, `frame_000001.png`, ... in the new
   directory (for `h264_black`, composited on black).
4. FFmpeg encodes the movie at the receipt's frame rate.
5. `ffprobe` reads it back. The frame count, frame rate, size, codec and (for ProRes) the alpha pixel format must
   match the request.
6. The receipt is validated again.

The directory then holds the frame PNGs, the movie and `movie.json`, which has the result.

## Result

| Field | Meaning |
|---|---|
| `path`, `sha256` | The movie file |
| `codec` | The codec used |
| `media_receipt_sha256` | The hash of the receipt the movie was made from |
| `stream` | What `ffprobe` reported: codec, pixel format, frame count, frame rate, size |
| `execution` | How the FFmpeg run went |
| `visual_acceptance` | `unreviewed`. The movie is for a person to watch. |
| `launches_applications` | `true` |

The directory must not exist. If FFmpeg or the checks fail after the directory exists, the call reports an unknown
outcome; look at the directory before you retry with a new one. Missing `ffmpeg` or `ffprobe` is refused with a message
to install them, before the directory is made.
