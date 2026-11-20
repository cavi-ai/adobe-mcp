# Configuration

The servers are configured with environment variables, set in the `env` of each entry in the MCP client.

| Variable | Used by | Meaning |
|---|---|---|
| `CAVI_ADOBE_WORKSPACE` | every server | Required. Absolute directory every tool path is relative to and must stay inside. |
| `CAVI_PHOTOSHOP_APP` | Photoshop | The Photoshop `.app` to use. |
| `CAVI_ILLUSTRATOR_APP` | Illustrator | The Illustrator `.app` to use. |
| `CAVI_ANIMATE_APP` | Animate | The Animate 2024 `.app` to use. |

Without an override each server uses the newest installed version under `/Applications` with the application's
bundle identifier. An override is final: a path that names another application is refused with
`APP_IDENTITY_MISMATCH` and is never replaced by another install. Dimension has no override: it is driven only at
`/Applications/Adobe Dimension/Adobe Dimension.app`.

| Application | Bundle identifier |
|---|---|
| Photoshop | `com.adobe.Photoshop` |
| Illustrator | `com.adobe.illustrator` |
| Animate 2024 | `com.adobe.Adobe-Animate-2024.application` |
| Dimension | `com.adobe.dimension` |

## Limits

| Setting | Value |
|---|---|
| Photoshop and Illustrator command time | 45 seconds; 120 seconds for the Photoshop generative tools |
| Animate command time | 60 seconds |
| Dimension helper time | 30 seconds; 60 seconds to start a render |
| Native helper compile time | 300 seconds |
| Canvas and resize size | 1 to 2880 px per side |
| Generative Upscale input | at most 1440 px per side |
| Generative Fill prompt | at most 1000 characters |

## Records in the workspace

The servers create private records inside the workspace. Keep them private. Do not edit or delete them while an
operation is unresolved.

| Directory | Server | Holds |
|---|---|---|
| `.adobe-authoring/` | Photoshop and Illustrator | Session records, private input copies of sources, canvas recipes and recovery archives. |
| `.animate-authoring/` | Animate | Session records, the pending operation, operation records, the compiled helper, staging for new documents and recovery archives. |
| `.dimension-authoring/` | Dimension | Render job records, the active job, the compiled helper and the lock. |

The compiled Swift helpers are cached by the SHA-256 of their source and checked against a receipt before each use.

A pending Photoshop or Illustrator operation is tracked in the system temporary directory, under a lock directory
per user and application, so it covers every workspace.
