"""Bounded, explicit After Effects renders and free FFmpeg review exports."""

import json
import os
import re
import shutil
import signal
import subprocess
import time
from fractions import Fraction
from pathlib import Path

from . import art_media as media

MAX_OUTPUT_BYTES = 512 * 1024 * 1024


class MediaOutcomeUnknown(RuntimeError):
    """A native command started; inspect its new directory before another action."""


def run(command, directory, seconds=300):
    """Run one task-owned process group with bounded time, memory and disk logging."""
    journal = directory / "execution.json"
    state = {"version": 1, "state": "pending", "arguments_sha256": __import__("hashlib").sha256(
        json.dumps(command).encode()).hexdigest(), "seconds_limit": seconds,
        "process_group_gib_limit": 8, "output_bytes_limit": MAX_OUTPUT_BYTES, "retry_safe": False}
    media.write(journal, state)
    start, peak, stopped = time.monotonic(), 0, None
    with (directory / "native.log").open("wb") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            while process.poll() is None:
                stats = subprocess.run(["/bin/ps", "-axo", "pgid=,rss="], capture_output=True,
                                       text=True, check=True, timeout=3)
                rss = sum(int(fields[1]) for line in stats.stdout.splitlines()
                          if len(fields := line.split()) == 2 and int(fields[0]) == process.pid)
                peak = max(peak, rss / (1024 * 1024))
                output_bytes = sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())
                if output_bytes > MAX_OUTPUT_BYTES:
                    stopped = "output_limit"
                    break
                if peak > 8 or time.monotonic() - start > seconds or log.tell() > 16 * 1024 * 1024:
                    stopped = "memory" if peak > 8 else "time_or_log_limit"
                    break
                time.sleep(.1)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
            state.update(state="unknown" if stopped or process.returncode else "exited",
                         exit_code=process.returncode, stopped_by_guard=stopped,
                         peak_process_group_gib=peak, elapsed_seconds=time.monotonic() - start)
            journal.write_text(json.dumps(state, indent=2) + "\n")
    if stopped or process.returncode:
        raise MediaOutcomeUnknown("Native command did not finish successfully; inspect execution.json and native.log")
    if sum(p.stat().st_size for p in directory.rglob("*") if p.is_file()) > MAX_OUTPUT_BYTES:
        state.update(state="unknown", stopped_by_guard="output_limit")
        journal.write_text(json.dumps(state, indent=2) + "\n")
        raise MediaOutcomeUnknown("Native output exceeded the byte budget; inspect its directory")
    return state


def ae_render(workspace, recipe, directory):
    """Render an existing selected AEP composition to a new PNG sequence."""
    source, target = media.inside(workspace, recipe), media.inside(workspace, directory)
    spec = media.load(source)
    if (not isinstance(spec, dict) or spec.get("version") != 1
            or set(spec) != {"version", "name", "project", "dependencies", "composition", "start", "end",
                             "fps", "loop", "alpha", "output_module"}
            or type(spec["start"]) is not int or type(spec["end"]) is not int
            or not 0 <= spec["start"] <= spec["end"] or spec["end"] - spec["start"] >= 1000
            or type(spec["loop"]) is not bool or spec["alpha"] != "straight"
            or not isinstance(spec["composition"], str) or not 1 <= len(spec["composition"]) <= 128
            or not isinstance(spec["output_module"], str) or not 1 <= len(spec["output_module"]) <= 128
            or any(ord(c) < 32 for c in spec["composition"] + spec["output_module"])
            or not isinstance(spec["dependencies"], list) or len(spec["dependencies"]) > 64):
        raise ValueError("Declare one bounded composition, inclusive range, rational FPS, dependencies and RGBA PNG output module")
    media.name(spec["name"])
    fps = media.rate(spec["fps"])
    if (spec["end"] - spec["start"] + 1) * fps["denominator"] / fps["numerator"] > 300:
        raise ValueError("Render exceeds 300 seconds of animation")
    project = media.inside(workspace, spec["project"])
    if project.suffix.lower() != ".aep":
        raise ValueError("Use an existing AEP project")
    inputs = [source, project, *(media.inside(workspace, p) for p in spec["dependencies"])]
    fingerprints = {str(p): media.digest(p) for p in inputs}
    binaries = sorted(Path("/Applications").glob("Adobe After Effects */aerender"))
    if len(binaries) != 1 or not os.access(binaries[0], os.X_OK):
        raise ValueError("Exactly one executable Adobe After Effects aerender must be installed")
    target.mkdir(parents=True, exist_ok=False)
    command = [str(binaries[0]), "-project", str(project), "-comp", spec["composition"],
               "-s", str(spec["start"]), "-e", str(spec["end"]), "-OMtemplate", spec["output_module"],
               "-output", str(target / "frame_[#####].png")]
    try:
        execution = run(command, target)
        if any(media.digest(p) != sha for p, sha in fingerprints.items()):
            raise MediaOutcomeUnknown("An AEP render input changed during execution")
        output = sorted(target.glob("frame_*.png"))
        numbers = [int(match[1]) for p in output if (match := re.fullmatch(r"frame_\[(\d+)\]\.png", p.name))]
        if not numbers:
            numbers = [int(match[1]) for p in output if (match := re.fullmatch(r"frame_(\d+)\.png", p.name))]
        count = spec["end"] - spec["start"] + 1
        if len(output) != count or len(numbers) != count or numbers != list(range(numbers[0], numbers[0] + count)):
            raise MediaOutcomeUnknown("Render did not produce the complete contiguous declared PNG range")
        ingest = {"version": 1, "name": spec["name"], "fps": fps, "loop": spec["loop"],
                  "alpha": "straight", "sources": [str(p) for p in inputs],
                  "frames": [{"name": f"Frame{i:06}", "path": str(p)} for i, p in enumerate(output)]}
        media.write(target / "frames.json", ingest)
        result = media.ingest(workspace, str(target / "frames.json"), str(target / "handoff"))
        return {**result, "execution": execution, "composition": spec["composition"],
                "timing_authority": "caller_declared", "dependencies_authority": "caller_declared",
                "launches_applications": True}
    except (OSError, ValueError) as exc:
        raise MediaOutcomeUnknown("Render started; inspect its directory before continuing: " + str(exc)) from exc


def review_movie(workspace, receipt, directory, codec="prores_alpha"):
    """Create an alpha-preserving MOV or explicitly opaque MP4 from pinned frames."""
    media.validate(workspace, receipt)
    data, target = media.load(media.inside(workspace, receipt)), media.inside(workspace, directory)
    if codec not in ("prores_alpha", "h264_black"):
        raise ValueError("Select prores_alpha or explicitly black-composited h264_black")
    if len({tuple(f["rect"][2:]) for f in data["frames"]}) != 1:
        raise ValueError("Review frames must share dimensions")
    media.export_budget(data)
    binary, probe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not binary or not probe:
        raise ValueError("Install the free FFmpeg and ffprobe executables")
    target.mkdir(parents=True, exist_ok=False)
    for index, frame in enumerate(data["frames"]):
        with media._crop(workspace, frame) as crop:
            if codec == "h264_black":
                from PIL import Image
                background = Image.new("RGBA", crop.size, (0, 0, 0, 255))
                background.alpha_composite(crop)
                background.convert("RGB").save(target / f"frame_{index:06}.png")
                background.close()
            else:
                crop.save(target / f"frame_{index:06}.png")
    fps = data["fps"]
    movie = target / ("review.mov" if codec == "prores_alpha" else "review.mp4")
    command = [binary, "-nostdin", "-n", "-v", "error", "-framerate", f"{fps['numerator']}/{fps['denominator']}",
               "-i", str(target / "frame_%06d.png"), "-frames:v", str(len(data["frames"]))]
    command += (["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le"] if codec == "prores_alpha"
                else ["-c:v", "libx264", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-pix_fmt", "yuv420p"])
    try:
        execution = run([*command, str(movie)], target)
        checked = subprocess.run([probe, "-v", "error", "-count_frames", "-select_streams", "v:0",
                                  "-show_entries", "stream=codec_name,pix_fmt,nb_read_frames,avg_frame_rate,width,height",
                                  "-of", "json", str(movie)], capture_output=True, text=True, check=True, timeout=15)
        stream, = json.loads(checked.stdout)["streams"]
        width, height = data["frames"][0]["rect"][2:]
        expected_size = (width, height) if codec == "prores_alpha" else (width + width % 2, height + height % 2)
        if (int(stream["nb_read_frames"]) != len(data["frames"])
                or Fraction(stream["avg_frame_rate"]) != Fraction(fps["numerator"], fps["denominator"])
                or (stream["width"], stream["height"]) != expected_size
                or stream["codec_name"] != ("prores" if codec == "prores_alpha" else "h264")
                or (codec == "prores_alpha" and not stream["pix_fmt"].startswith("yuva"))):
            raise MediaOutcomeUnknown("Movie frame count or alpha format differs from the requested export")
        media.validate(workspace, receipt)
        result = {"path": str(movie), "sha256": media.digest(movie), "codec": codec,
                  "media_receipt_sha256": media.digest(media.inside(workspace, receipt)), "stream": stream,
                  "execution": execution, "visual_acceptance": "unreviewed"}
        result["launches_applications"] = True
        media.write(target / "movie.json", result)
        return result
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise MediaOutcomeUnknown("Movie export started; inspect its directory before continuing: " + str(exc)) from exc
