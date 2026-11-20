# Installation

## Requirements

- macOS, Python 3.15 or newer and [uv](https://docs.astral.sh/uv/).
- The Adobe applications you use, installed, licensed and signed in.
- Automation access for the MCP host to control Photoshop, Illustrator and Animate.
- Animate and Dimension use a small Swift helper that is compiled on first use. It needs the Swift command-line
  compiler (`xcode-select --install`).
  - Animate's helper needs existing Accessibility access.
  - Dimension's helper needs existing Accessibility and Screen Recording access, and permission to post input events.
    It controls Dimension through its visible windows.
- `ffmpeg` and `ffprobe` on `PATH` or in Homebrew's directory for review movies.

The servers never request a permission. A missing permission is reported and the call stops.

## Install

```sh
uv tool install cavi-adobe-mcp
```

The package installs three commands:

| Command | Application |
|---|---|
| `cavi-adobe-authoring-mcp` | Photoshop, Illustrator and review movies |
| `cavi-animate-mcp` | Animate |
| `cavi-dimension-mcp` | Dimension |

## Register a server

Add the servers you need to your MCP client and set the workspace:

```json
{
  "mcpServers": {
    "adobe-authoring": {
      "command": "cavi-adobe-authoring-mcp",
      "env": {"CAVI_ADOBE_WORKSPACE": "/Users/me/Art"}
    },
    "adobe-animate": {
      "command": "cavi-animate-mcp",
      "env": {"CAVI_ADOBE_WORKSPACE": "/Users/me/Art"}
    },
    "adobe-dimension": {
      "command": "cavi-dimension-mcp",
      "env": {"CAVI_ADOBE_WORKSPACE": "/Users/me/Art"}
    }
  }
}
```

`CAVI_ADOBE_WORKSPACE` is required and must be an absolute directory. See [Configuration](../reference/configuration.md)
for the other variables.

## Before the first call

Open the application yourself and clear any sign-in, licence or Generative Credits prompt. The Photoshop,
Illustrator and Animate tools need the application to be running already. Dimension can be started with
`dimension_launch`.
