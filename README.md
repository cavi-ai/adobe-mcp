# Cavi Adobe MCP

## Keep the magic editable.

![A paper theatre with a fox arranging a whale and stars](https://raw.githubusercontent.com/cavi-ai/adobe-mcp/main/media/adobe-mcp-hero.png)

[![PyPI weekly downloads](https://img.shields.io/pypi/dw/cavi-adobe-mcp?label=PyPI%20downloads%2Fweek)](https://pypi.org/project/cavi-adobe-mcp/)

Tell your assistant what to change. Keep the Photoshop layers, Illustrator paths and Animate timeline.

Cavi Adobe MCP connects your MCP client to native Adobe projects through application-specific local servers, with editable working copies and supported animation and render handoffs.

Ask for outcomes in your own words:

- “Make a working copy of this illustration, add editable text and export a preview.”
- “Build an Animate timeline from this artwork, then export its frames.”
- “Export the Animate frames and make a review movie.”

Four local servers cover different jobs: Photoshop and Illustrator authoring and review movies; Animate timeline authoring; Character Animator project and scene workflows; and Dimension inspection and rendering. Their capabilities differ by application.

## The Adobe suite

Each larger application has its own package. Install the ones you use.

| Applications | Package | Install |
|---|---|---|
| Photoshop, Illustrator, Animate, Character Animator, Dimension | Cavi Adobe MCP (this repository) | `uv tool install cavi-adobe-mcp` |
| After Effects | [Cavi After Effects MCP](https://github.com/cavi-ai/after-effects-mcp) | `uv tool install cavi-after-effects-mcp` |
| Premiere Pro | [Cavi Premiere MCP](https://github.com/cavi-ai/premiere-mcp) | `uv tool install cavi-premiere-mcp` |
| Substance 3D Painter, Designer, Sampler | [Cavi Substance MCP](https://github.com/cavi-ai/substance-mcp) | `uv tool install cavi-substance-mcp` |

## Requirements

- macOS, Python 3.15+ and [uv](https://docs.astral.sh/uv/).
- The licensed Adobe applications you use, installed and signed in.
- Automation access for the MCP host to control the application (Photoshop,
  Illustrator, Animate).
- Animate and Character Animator also use a small native helper compiled on first use,
  which needs the Swift command-line compiler (`xcode-select --install`) and existing
  Accessibility access. Character Animator capture also needs Screen Recording access.
- `ffmpeg` on `PATH` for review movies.

## Install

```sh
uv tool install cavi-adobe-mcp
```

The package installs four commands:

| Command | Application |
|---|---|
| `cavi-adobe-authoring-mcp` | Photoshop, Illustrator and review movies |
| `cavi-animate-mcp` | Animate |
| `cavi-character-animator-mcp` | Character Animator |
| `cavi-dimension-mcp` | Dimension |

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
| `CAVI_PHOTOSHOP_APP` | The Photoshop `.app` to use. |
| `CAVI_ILLUSTRATOR_APP` | The Illustrator `.app` to use. |
| `CAVI_ANIMATE_APP` | The Animate 2024 `.app` to use. |
| `CAVI_CHARACTER_ANIMATOR_APP` | The Character Animator `.app` to use. |

Without an override each server uses the newest installed version under `/Applications`
with the application's bundle identifier. An override is final: one that names another
application is refused with `APP_IDENTITY_MISMATCH`. Dimension is driven only at
`/Applications/Adobe Dimension/Adobe Dimension.app`.

Ownership records live inside the workspace (`.adobe-authoring/`, `.animate-authoring/`);
keep them private.

<details>
<summary>Application reference</summary>

Authoring supports inspection, owned copies, canvas and layer edits, text, paths, masks and export. Animate supports editable timelines and frame export. Character Animator supports project, scene, capture and export workflows. Dimension supports inspection and rendering. FFmpeg can create review movies.

Keep workspace ownership records private. Each native operation is recorded before it runs. If an operation reports an unknown outcome, inspect the application and outputs or reconcile the recorded operation before trying again.

For a closed Character Animator project, prepare a copy with `project_prepare_copy`, open that prepared copy, inspect its project root and claim it with `session_control` before editing. A WAV handoff receipt does not confirm import: check that the audio appears in the project's Audio rows. After a process exits, the operation control and session control tools provide explicit reconciliation and lease release actions; they preserve unknown outcomes and project files.

</details>

## Development

```sh
uv sync --locked --dev
uv run --locked python -m unittest discover -s tests
uv run --locked ruff check .
```

Evaluation configuration is in [evals](evals/).

## License

MIT. Adobe, Photoshop, Illustrator, After Effects, Premiere Pro, Substance 3D, Animate, Character Animator and Dimension are trademarks of Adobe Inc.; this project is not affiliated with Adobe.
