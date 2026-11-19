"""Bounded native process runs and free FFmpeg review exports."""

import json
from fractions import Fraction

from cavi_mcp_core import apps, proc
from cavi_mcp_core.media import MediaOutcomeUnknown, run

from . import art_media as media


def review_movie(workspace, receipt, directory, codec="prores_alpha"):
    """Create an alpha-preserving MOV or explicitly opaque MP4 from pinned frames."""
    media.validate(workspace, receipt)
    data, target = media.load(media.inside(workspace, receipt)), media.inside(workspace, directory)
    if codec not in ("prores_alpha", "h264_black"):
        raise ValueError("Select prores_alpha or explicitly black-composited h264_black")
    if len({tuple(f["rect"][2:]) for f in data["frames"]}) != 1:
        raise ValueError("Review frames must share dimensions")
    media.export_budget(data)
    binary, probe = apps.program("ffmpeg"), apps.program("ffprobe")
    if not binary or not probe:
        raise ValueError("Install the free FFmpeg and ffprobe executables")
    binary, probe = str(binary), str(probe)
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
        checked = proc.run_sync([probe, "-v", "error", "-count_frames", "-select_streams", "v:0",
                                 "-show_entries", "stream=codec_name,pix_fmt,nb_read_frames,avg_frame_rate,width,height",
                                 "-of", "json", str(movie)], 15)
        if checked.returncode:
            raise OSError("ffprobe failed: " + proc.diagnostics(checked))
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
    except (OSError, ValueError) as exc:
        raise MediaOutcomeUnknown("Movie export started; inspect its directory before continuing: " + str(exc)) from exc
