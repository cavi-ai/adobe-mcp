# Security model

## The servers act with your permissions

The servers control Adobe applications with the permissions of the MCP host: Automation access for Photoshop,
Illustrator and Animate, Accessibility access for the Animate and Dimension helpers, and Screen Recording access and
input events for Dimension's helper. They can do what you can do in those applications. Connect them only to clients
you trust. Tools that change a document are annotated as destructive for that reason.

The servers do not grant themselves access. A missing permission is reported (`ACCESSIBILITY_UNAVAILABLE`,
`PERMISSION_UNAVAILABLE`) and nothing is requested. A sign-in, licence or permission prompt in an application stops
the operation (`PROMPT_BLOCKED`, `AUTHENTICATION_BOUNDARY`); no input is sent to it and no account is signed in.
Starting a server and the status tools never launch an application. Only `dimension_launch` starts one, and only the
fixed installed Dimension bundle.

## Fixed scripts and typed data

No caller-supplied script is run. Photoshop and Illustrator get a fixed JSX driver, Animate a fixed JSFL driver and
the helpers fixed Swift sources. Callers supply typed data that the server validates first: bounded numbers, names of
1 to 128 characters, layer paths of bounded depth, and a closed list of actions. A request with an unknown field is
refused.

## Generative tools and Adobe's service

Generative Fill and Generative Upscale send pixels to Adobe and spend generative credits on the signed-in Creative
Cloud account. The tool annotations mark both as open-world. Remove Background adds a layer mask to one layer of the
owned copy.

- Connect a client to the authoring server only if you accept that it can start these calls and spend credits.
- Prompts are limited to 1000 printable characters, and the call has a 120 second limit.
- The server does not sign in, accept terms or acknowledge Photoshop's one-time Generative Credits notice. You
  acknowledge it in the application. Until then a call times out with an unknown outcome.
- A call is never retried automatically. A repeated call spends credits again.

## Files

Every path is relative to `CAVI_ADOBE_WORKSPACE`. Paths with `..`, symbolic links or control characters are refused
and so are paths outside the workspace. Outputs (copies, exports, render directories, frame directories, movie
directories) are written only to paths that do not exist yet; nothing is overwritten. Exports are staged and linked into
place, and receipts are created exclusively.

Sources are copied to private input copies and hashed before and after. The source of a session is never opened for
writing. Photoshop and Illustrator copies are saved only by the owning session, and the hash of the copy is checked
before every later change.

SVG sources and exports are rejected if they contain scripts, `foreignObject`, event handlers, entity declarations,
stylesheet instructions or external references.

## Ownership and records

A session owns one document and one application process. A change is refused if the process, the source, the copy or
the open document differs from what the last operation recorded. One command runs at a time for each application.

Every native command is recorded before it is dispatched, and an unknown outcome blocks further changes until it is
resolved. Recovery actions need the hashes you observed. Records are in `.adobe-authoring/`, `.animate-authoring/` and
`.dimension-authoring/` in the workspace and hold file paths and hashes; keep them private.
