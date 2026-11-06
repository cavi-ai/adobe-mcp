"""Evaluate background native reads and headless media tools; unavailable means failure."""

import argparse
import asyncio
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from evals.run import BACKGROUND_TOOLS

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_TOOLS = frozenset.union(*BACKGROUND_TOOLS.values()) | {
    "after_effects_render", "media_review_movie"}


def inside(workspace, value):
    if Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError("Use workspace-relative native fixture paths without traversal")
    path = (workspace / value).resolve(strict=True)
    if not path.is_relative_to(workspace) or not path.is_file():
        raise ValueError("Native fixtures must be regular files inside the selected workspace")
    return path


def check_prompts(value):
    if isinstance(value, dict):
        if value.get("authentication_or_permission_prompt") is True:
            raise RuntimeError("Native application reports an authentication or permission prompt")
        for child in value.values():
            check_prompts(child)
    elif isinstance(value, list):
        for child in value:
            check_prompts(child)


class NativeRun:
    def __init__(self, workspace, output, env):
        self.workspace, self.output, self.env = workspace, output, env
        self.calls = []

    async def call(self, client, tool, arguments=None):
        if tool not in ALLOWED_TOOLS:
            raise RuntimeError(f"Tool {tool} is unavailable in background native evaluation")
        reply = await client.call_tool(tool, arguments or {})
        record = {"tool": tool, "arguments": arguments or {},
                  "reply": reply.model_dump(mode="json", by_alias=True)}
        self.calls.append(record)
        (self.output / "native-calls.json").write_text(json.dumps(self.calls, indent=2) + "\n")
        check_prompts(reply.structured_content)
        if reply.is_error:
            raise RuntimeError(f"Native tool {tool} failed: {reply.content}")
        return reply.structured_content

    def server(self, module):
        return StdioServerParameters(command=sys.executable,
            args=["-m", f"cavi_adobe_mcp.{module}_server"], env=self.env)

    async def check_installed(self):
        for module, tools in (
            ("authoring", (("adobe_status", {"application": "photoshop"}),
                           ("adobe_status", {"application": "illustrator"}))),
            ("animate", (("animate_status", {}),)),
            ("character_animator", (("character_status", {}),)),
            ("dimension", (("dimension_status", {}),)),
        ):
            async with stdio_client(self.server(module)) as (reader, writer), \
                    ClientSession(reader, writer, read_timeout_seconds=180) as client:
                await client.initialize()
                for tool, arguments in tools:
                    status = await self.call(client, tool, arguments)
                    ready = (status.get("connected") if tool == "character_status" else
                             status.get("native", status).get("running"))
                    if ready is not True:
                        raise RuntimeError(f"{tool} requires an already-running licensed app")

    async def render_media(self, recipe):
        async with stdio_client(self.server("authoring")) as (reader, writer), \
                ClientSession(reader, writer, read_timeout_seconds=330) as client:
            await client.initialize()
            rendered = await self.call(client, "after_effects_render", {
                "recipe": recipe, "directory": self.destination("after-effects")})
            movie = await self.call(client, "media_review_movie", {
                "receipt": rendered["receipt"], "directory": self.destination("movie")})
            if int(movie["stream"]["nb_read_frames"]) != rendered["frame_count"]:
                raise RuntimeError("Native review movie frame count differs from the render")

    def destination(self, name):
        return self.output.relative_to(self.workspace).as_posix() + "/" + name


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--ae-recipe", required=True, help="Workspace-relative real AEP render recipe")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("Native evaluation requires macOS and the installed licensed Adobe applications")
    try:
        workspace = args.workspace.resolve(strict=True)
        if not args.workspace.is_absolute() or not workspace.is_dir():
            raise ValueError("Select an existing absolute native fixture workspace")
        recipe = inside(workspace, args.ae_recipe)
        if recipe.suffix != ".json":
            raise ValueError("Select a real AEP recipe JSON")
        if shutil.which(os.environ.get(
                "MCPEVAL_EXECUTABLE", str(ROOT / "node_modules/.bin/mcpeval"))) is None:
            raise ValueError("Install MCP Eval or set MCPEVAL_EXECUTABLE before native evaluation")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    output = Path(tempfile.mkdtemp(prefix="native-eval-", dir=workspace))
    data = output / "data"
    data.mkdir()
    env = dict(os.environ, CAVI_ADOBE_WORKSPACE=str(workspace), CAVI_ADOBE_DATA=str(data))
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    sources = (sorted((ROOT / "src").rglob("*")) + sorted((ROOT / "evals").glob("*"))
               + sorted((ROOT / "tests").glob("*native.py"))
               + [ROOT / "tests/native_recovery_server.py"])
    metadata = {"source_sha": source, "source_sha256": {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sources if path.is_file() and path.suffix in {".py", ".jsx", ".jsfl", ".swift", ".json"}
    }, "fixtures_sha256": {str(p.relative_to(workspace)): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in (recipe,)}}
    applications = {}
    for app in Path("/Applications").glob("Adobe*/*.app"):
        info_path = app / "Contents/Info.plist"
        if info_path.is_file():
            info = plistlib.loads(info_path.read_bytes())
            if info.get("CFBundleIdentifier") in {
                "com.adobe.Photoshop", "com.adobe.illustrator", "com.adobe.Adobe-Animate-2024.application",
                "com.adobe.Character-Animator.application", "com.adobe.dimension",
                "com.adobe.AfterEffects.application",
            }:
                applications[info["CFBundleIdentifier"]] = info.get("CFBundleShortVersionString")
    metadata["installed_versions"] = applications
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    native = NativeRun(workspace, output, env)
    summary = {"passed": False, "scope": "background_native", "artifacts": str(output),
               "coverage": {"photoshop": "installation_and_process_status",
                            "illustrator": "installation_and_process_status",
                            "animate": "installation_and_process_status",
                            "character_animator": "native_inspection", "dimension": "native_inspection",
                            "after_effects": "headless_render", "ffmpeg": "headless_review_movie"}}
    try:
        asyncio.run(native.check_installed())
        inspected = subprocess.run([sys.executable, str(ROOT / "evals/run.py"),
                                    "--mode", "native-background"], cwd=ROOT, env=env,
                                   capture_output=True, text=True, timeout=900, check=False)
        (output / "inspection.log").write_text(inspected.stdout + inspected.stderr)
        if inspected.returncode:
            raise RuntimeError("Native inspection evaluation failed")
        summary["inspection"] = json.loads(inspected.stdout)
        asyncio.run(native.render_media(args.ae_recipe))
        summary["passed"] = True
    except Exception as exc:  # noqa: BLE001 -- retain native failure evidence and exit nonzero
        summary["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
