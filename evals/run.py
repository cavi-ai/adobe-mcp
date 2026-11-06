"""Evaluate Adobe MCP catalogs and tools with synthetic native adapters."""

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("all", "protocol", "simulated"), default="all")
    args = parser.parse_args()
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
                suffix = "" if mode == "protocol" else ".simulated"
                command = (["-m", f"cavi_adobe_mcp.{server}_server"] if mode == "protocol" else
                           [str(ROOT / "evals/simulated_server.py"), server])
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
