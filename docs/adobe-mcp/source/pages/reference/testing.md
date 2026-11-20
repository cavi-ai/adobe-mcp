# Testing and evaluation

No Adobe application is needed for the checks below, except the optional native evaluations.

## Unit tests

```sh
uv sync --locked --dev
uv run --locked ruff check .
uv run --locked python -m unittest discover -s tests
uv run --locked python -m scripts.docs.build
uv run --locked python -m scripts.docs.verify
```

The tests drive the real authoring, Animate and Dimension code with a scripted native bridge and synthetic files. They
establish the servers' own rules, not Adobe's behavior. They cover:

- Photoshop and Illustrator authoring: owned copies that leave the source unchanged, canvas recipes and depth,
  resizing, layer edits with expected names, groups, placement, rectangles, paths, text and masks, export hashes and
  receipts, refusal of paths that leave the workspace, unknown outcomes that block later changes, and changed sources,
  copies or processes;
- the generative tools: `adobe_generative_fill` validates its prompt and bounds before dispatch and sends an empty
  prompt with no bounds as a whole-canvas request; `adobe_remove_background` targets one exact layer path and name;
  `adobe_generative_upscale` dispatches once for an owned copy; all three are refused for Illustrator before any native
  request is sent, and each returns a `document` snapshot with `retry_safe` `false`;
- Animate: sessions, timeline edits, frame export and receipts, the recovery actions, and the native chooser handling;
- Dimension: render submission, inspection, the RGBA handoff and the receipt;
- media: the media receipt and the review movie;
- the helper build: one build per source, a tampered or symbolic-link cache refused, and timeouts;
- application lookup and the evaluation contracts, including that unknown arguments are rejected at the MCP boundary.

Tests that compile or run a Swift helper run only on macOS with `swiftc`.

## Evaluations

```sh
npm install --no-audit --no-fund --package-lock=false
npm run eval
```

`npm run eval` runs `evals/run.py`, which starts each server over stdio and runs the [MCP
Eval](https://github.com/cavi-ai/mcp-eval) probes. `MCPEVAL_EXECUTABLE` selects another `mcpeval` binary. The reports go
to `.tmp/evals/<run>/`.

| Server | Protocol manifest limits |
|---|---|
| Authoring | at most 20 tools and 24,000 schema bytes; at most 9,000 tokens in total and 3,000 per tool |
| Animate | at most 10 tools and 12,000 schema bytes; at most 6,000 tokens in total and 3,000 per tool |
| Dimension | at most 6 tools and 5,000 schema bytes; at most 3,000 tokens in total and per tool |

| Mode | Script | What it runs |
|---|---|---|
| `protocol` | `npm run eval:protocol` | Each server with its `evals/<server>.manifest.json`: the tool and token budgets above, pagination, the declared surfaces and a refusal of an unknown protocol version. |
| `simulated` | `npm run eval:simulated` | The production tools with a synthetic native adapter, against the `inspection` manifests. |
| `native-background` | `uv run --locked python evals/run.py --mode native-background` | The `background` and `inspection` manifests against running applications. Only read-only status and inspect tools are accepted. |

`npm run eval` runs the `protocol` and `simulated` modes. `npm run eval:native` runs `evals/native.py` against running,
signed-in applications. It reads status from each server, refuses tools that need the user interface, and also makes a
review movie from synthetic frames. It does not call the generative tools.

## Checked against Adobe applications

The unit tests and the protocol and simulated evaluations do not start an Adobe application, so they do not cover how
Photoshop, Illustrator, Animate or Dimension behave. In particular, no test calls Adobe's generative service, so the
account, the credits and the Generative Credits notice are not exercised. A tool that fails in your install is a bug to
report. See [Version and support](../release/version-and-support.md).
