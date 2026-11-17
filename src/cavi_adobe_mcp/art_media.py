"""Local PNG animation, atlas and editorial handoffs. No application is launched."""

from __future__ import annotations

import math
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

from cavi_mcp_core.media import (  # noqa: F401  (the receipt, used through this module)
    SCHEMA,
    digest,
    frames,
    ingest,
    inside,
    load,
    name,
    rate,
    validate,
    write,
)
from PIL import Image

MAX_EXPANDED_PIXELS = 64 * 1024 * 1024


def _crop(workspace, frame):
    x, y, width, height = frame["rect"]
    with Image.open(inside(workspace, frame["path"])) as image:
        return image.crop((x, y, x + width, y + height))


def export_budget(data):
    if sum(frame["rect"][2] * frame["rect"][3] for frame in data["frames"]) > MAX_EXPANDED_PIXELS:
        raise ValueError("Export exceeds 256 MiB of expanded RGBA frames; split the edit")


def pack(workspace, receipt, directory, columns=8, padding=2):
    """Pack fixed cells without trimming or rotation; preserve exact RGBA pixels."""
    validate(workspace, receipt)
    data, target = load(inside(workspace, receipt)), inside(workspace, directory)
    if (type(columns) is not int or not 1 <= columns <= 64 or type(padding) is not int
            or not 0 <= padding <= 32):
        raise ValueError("Use 1..64 columns and 0..32 pixels of transparent padding")
    columns = min(columns, len(data["frames"]))
    cell_w = max(f["rect"][2] for f in data["frames"]) + padding * 2
    cell_h = max(f["rect"][3] for f in data["frames"]) + padding * 2
    width, height = columns * cell_w, math.ceil(len(data["frames"]) / columns) * cell_h
    if max(width, height) > 8192 or width * height > 32000000:
        raise ValueError("Atlas exceeds the texture budget; reduce frames or cell size")
    target.mkdir(parents=True, exist_ok=False)
    atlas_path = target / "atlas.png"
    atlas, result = Image.new("RGBA", (width, height)), []
    for index, frame in enumerate(data["frames"]):
        x, y = index % columns * cell_w + padding, index // columns * cell_h + padding
        with _crop(workspace, frame) as crop:
            atlas.paste(crop, (x, y))
        result.append({**frame, "path": str(atlas_path.relative_to(Path(workspace).resolve())),
                       "width": width, "height": height, "rect": [x, y, *frame["rect"][2:]]})
    atlas.save(atlas_path)
    atlas.close()
    for frame in result:
        frame["sha256"] = digest(atlas_path)
    validate(workspace, receipt)
    refs = [{"path": str(inside(workspace, receipt).relative_to(Path(workspace).resolve())),
             "sha256": digest(inside(workspace, receipt))}, *data.get("sources", []),
            *({"path": f["path"], "sha256": f["sha256"]} for f in data["frames"])]
    refs = list({item["path"]: item for item in refs}.values())
    write(target / "media.json", {**data, "frames": result, "sources": refs})
    return validate(workspace, str(target / "media.json"))


def editorial(workspace, receipt, directory, audio=None):
    """Export frame-exact FCP7 XML and PNG clips for Premiere/After Effects import."""
    validate(workspace, receipt)
    data, target = load(inside(workspace, receipt)), inside(workspace, directory)
    fps = rate(data["fps"])
    export_budget(data)
    n, d = fps["numerator"], fps["denominator"]
    if not (d == 1 and n in (24, 25, 30, 48, 50, 60) or d == 1001 and n in (24000, 30000, 60000)):
        raise ValueError("FCP7 XML supports the declared standard integer or NTSC rates only")
    sizes = {tuple(frame["rect"][2:]) for frame in data["frames"]}
    if len(sizes) != 1:
        raise ValueError("Editorial frames must share dimensions")
    audio_info = None
    if audio is not None:
        audio_path = inside(workspace, audio)
        with wave.open(str(audio_path), "rb") as stream:
            if (stream.getcomptype() != "NONE" or stream.getformat() == wave.WAVE_FORMAT_IEEE_FLOAT
                    or stream.getnchannels() not in (1, 2)):
                raise ValueError("Use mono or stereo PCM WAV")
            audio_info = {"path": str(audio_path), "sha256": digest(audio_path),
                          "samples": stream.getnframes(), "sample_rate": stream.getframerate(),
                          "channels": stream.getnchannels(), "depth": stream.getsampwidth() * 8}
        if audio_info["samples"] / audio_info["sample_rate"] + 1e-6 < len(data["frames"]) * d / n:
            raise ValueError("WAV is shorter than the animation; provide the intended edit explicitly")
    target.mkdir(parents=True, exist_ok=False)
    root = ET.Element("xmeml", version="5")
    sequence = ET.SubElement(root, "sequence", id="animation")
    def element(parent, tag, value):
        ET.SubElement(parent, tag).text = str(value)
    def xml_rate(parent):
        node = ET.SubElement(parent, "rate")
        element(node, "timebase", round(n / d))
        element(node, "ntsc", "TRUE" if d == 1001 else "FALSE")
    element(sequence, "name", data["name"])
    element(sequence, "duration", len(data["frames"]))
    xml_rate(sequence)
    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")
    width, height = next(iter(sizes))
    sample = ET.SubElement(ET.SubElement(video, "format"), "samplecharacteristics")
    for key, value in (("width", width), ("height", height), ("pixelaspectratio", "square"), ("fielddominance", "none")):
        element(sample, key, value)
    xml_rate(sample)
    track = ET.SubElement(video, "track")
    outputs = []
    for index, frame in enumerate(data["frames"]):
        path = target / f"frame_{index:06}.png"
        with _crop(workspace, frame) as crop:
            crop.save(path)
        clip = ET.SubElement(track, "clipitem", id=f"frame-{index}")
        for key, value in (("name", frame["name"]), ("enabled", "TRUE"), ("duration", 1),
                           ("start", index), ("end", index + 1), ("in", 0), ("out", 1), ("alphatype", "straight")):
            element(clip, key, value)
        xml_rate(clip)
        file = ET.SubElement(clip, "file", id=f"source-{index}")
        element(file, "name", path.name)
        element(file, "pathurl", path.as_uri())
        xml_rate(file)
        element(file, "duration", 1)
        characteristics = ET.SubElement(ET.SubElement(ET.SubElement(file, "media"), "video"), "samplecharacteristics")
        element(characteristics, "width", width)
        element(characteristics, "height", height)
        outputs.append({"path": str(path), "sha256": digest(path)})
    if audio_info:
        audio_node = ET.SubElement(media, "audio")
        for channel in range(audio_info["channels"]):
            clip = ET.SubElement(ET.SubElement(audio_node, "track"), "clipitem", id=f"audio-{channel}")
            for key, value in (("name", Path(audio_info["path"]).name), ("start", 0),
                               ("end", len(data["frames"])), ("in", 0), ("out", len(data["frames"]))):
                element(clip, key, value)
            xml_rate(clip)
            file = ET.SubElement(clip, "file", id=f"audio-file-{channel}")
            element(file, "pathurl", Path(audio_info["path"]).as_uri())
            sample = ET.SubElement(ET.SubElement(ET.SubElement(file, "media"), "audio"), "samplecharacteristics")
            element(sample, "samplerate", audio_info["sample_rate"])
            element(sample, "depth", audio_info["depth"])
            source = ET.SubElement(clip, "sourcetrack")
            element(source, "mediatype", "audio")
            element(source, "trackindex", channel + 1)
    xml_path = target / "sequence.xml"
    ET.ElementTree(root).write(xml_path, encoding="utf-8", xml_declaration=True)
    outputs.append({"path": str(xml_path), "sha256": digest(xml_path)})
    validate(workspace, receipt)
    if audio_info and digest(audio_info["path"]) != audio_info["sha256"]:
        raise RuntimeError("Audio changed during editorial export; inspect the output directory")
    report = {"schema": "cavi.editorial.v1", "media_receipt_sha256": digest(inside(workspace, receipt)),
              "format": "FCP7 XML", "fps": fps, "frames": len(data["frames"]), "audio": audio_info,
              "outputs": outputs, "native_import": "unverified", "launches_applications": False}
    write(target / "editorial.json", report)
    return report
