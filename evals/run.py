"""Evaluate Adobe MCP catalogs, simulated tools, or background native reads."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVERS = ("authoring", "animate", "character_animator", "dimension")
BACKGROUND_TOOLS = {
    # Document inspection selects native documents internally; status only is background-safe.
    "authoring": frozenset({"adobe_status"}),
    "animate": frozenset({"animate_status"}),
    "character_animator": frozenset({"character_status", "inspect"}),
    "dimension": frozenset({"dimension_status", "dimension_inspect"}),
}


def background_manifest(server, manifest):
    """Refuse probes outside the reviewed background read APIs before starting a server."""
    for case in manifest["probes"]:
        if case["access"] != "read_only":
            raise ValueError("Native background probes must be read-only")
        if case["probe"] == "workflow":
            tools = [step["tool"] for step in case["steps"]]
        elif case["probe"] in {"output-schema", "instruction-fidelity"}:
            tools = [case["tool"]]
        else:
            raise ValueError(f"Unsupported native background probe: {case['probe']}")
        if not set(tools) <= BACKGROUND_TOOLS[server]:
            raise ValueError(f"Unsupported native background tool in {server}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("all", "protocol", "simulated", "native-background"),
                        default="all")
    args = parser.parse_args()
    if args.mode == "native-background":
        for server in SERVERS:
            suffix = "background" if server in {"authoring", "animate"} else "inspection"
            background_manifest(server, json.loads(
                (ROOT / f"evals/{server}.{suffix}.manifest.json").read_text()))
    selected = os.environ.get("MCPEVAL_EXECUTABLE", str(ROOT / "node_modules/.bin/mcpeval"))
    executable = shutil.which(selected)
    if executable is None:
        raise SystemExit("Install MCP Eval with npm install or set MCPEVAL_EXECUTABLE")
    evaluator = Path(executable).resolve()
    reports = ROOT / ".tmp/evals"
    reports.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=f"{args.mode}-", dir=reports))
    version = subprocess.run([str(evaluator), "--version"], cwd=ROOT,
                             capture_output=True, text=True, timeout=30, check=True)
    metadata = {
        "evaluator_version": version.stdout.strip(),
        "evaluator_executable": str(evaluator),
        "evaluator_sha256": hashlib.sha256(evaluator.read_bytes()).hexdigest(),
        "native_source_revision": os.environ.get("MCPEVAL_SOURCE_REVISION"),
        "npm_wrapper_source": json.loads((ROOT / "package.json").read_text())["devDependencies"][
            "@cavi-ai/mcp-eval"],
        "mode": args.mode,
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    results = {}
    modes = ("protocol", "simulated") if args.mode == "all" else (args.mode,)
    with tempfile.TemporaryDirectory(prefix="adobe-eval-store-") as store:
        env = dict(os.environ, MCPEVAL_HOME=store)
        for mode in modes:
            for server in SERVERS:
                suffix = "" if mode == "protocol" else ".inspection"
                if mode == "native-background" and server in {"authoring", "animate"}:
                    suffix = ".background"
                command = ([str(ROOT / "evals/simulated_server.py"), server] if mode == "simulated"
                           else ["-m", f"cavi_adobe_mcp.{server}_server"])
                key = f"{server}-{mode}"
                with (output / f"{key}.json").open("w") as stream:
                    result = subprocess.run([
                        str(evaluator), "probe", "--server", f"adobe-{key}",
                        "--manifest", str(ROOT / f"evals/{server}{suffix}.manifest.json"),
                        "--format", "json", "--gate-only",
                        "--", sys.executable, *command,
                    ], cwd=ROOT, env=env, stdout=stream, check=False, timeout=300)
                results[key] = result.returncode
    summary = {"mcpeval_exits": results, "artifacts": str(output)}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))
    raise SystemExit(1 if any(results.values()) else 0)


if __name__ == "__main__":
    main()
