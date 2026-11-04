"""Local PNG animation, atlas and editorial handoffs. No application is launched."""

from __future__ import annotations

import hashlib
import json
import math
import re
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

SCHEMA = "cavi.animation-media.v1"
MAX_EXPANDED_PIXELS = 64 * 1024 * 1024


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inside(workspace, path):
    if not isinstance(path, (str, Path)) or not str(path) or "\x00" in str(path):
        raise ValueError("Use a nonempty workspace path")
    root = Path(workspace).resolve(strict=True)
    result = (root / path).resolve()
    if not result.is_relative_to(root) or result == root:
        raise ValueError("Use a path inside the shared workspace")
    return result


def load(path):
    if Path(path).stat().st_size > 4 * 1024 * 1024:
        raise ValueError("JSON must be at most 4 MiB")
    return json.loads(Path(path).read_text())


def write(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("Names must start with a letter and contain at most 64 ASCII letters, digits, _ or -")
    return value


def rate(value):
    if (not isinstance(value, dict) or set(value) != {"numerator", "denominator"}
            or any(type(x) is not int or not 1 <= x <= 120000 for x in value.values())
            or not 1 <= value["numerator"] / value["denominator"] <= 120):
        raise ValueError("Declare rational FPS numerator/denominator in 1..120 FPS")
    divisor = math.gcd(value["numerator"], value["denominator"])
    return {key: item // divisor for key, item in value.items()}


def frames(workspace, value, *, pinned):
    if not isinstance(value, list) or not 1 <= len(value) <= 1000:
        raise ValueError("Declare 1..1000 ordered frames")
    result, images, names, encoded, pixels = [], {}, set(), 0, 0
    for frame in value:
        required = {"name", "path", "sha256", "width", "height", "rect"} if pinned else {"name", "path"}
        if (not isinstance(frame, dict) or not required <= set(frame)
                or set(frame) - (required | {"rect"})):
            raise ValueError("Unexpected or missing frame fields")
        label = name(frame["name"])
        if label in names:
            raise ValueError("Frame names must be unique")
        names.add(label)
        path = inside(workspace, frame["path"])
        if path not in images:
            encoded += path.stat().st_size
            if encoded > 256 * 1024 * 1024:
                raise ValueError("Encoded PNG inputs exceed 256 MiB")
            with path.open("rb") as stream:
                header = stream.read(26)
            if len(header) != 26 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[24:26] != b"\x08\x06":
                raise ValueError("Use 8-bit RGBA PNGs; higher bit depths require an explicit source export")
            with Image.open(path) as image:
                if image.format != "PNG" or image.mode != "RGBA" or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Use single-image RGBA PNGs with declared straight alpha")
                width, height = image.size
                pixels += width * height
                if max(width, height) > 8192 or pixels > 32000000:
                    raise ValueError("PNG inputs exceed the texture budget")
                image.load()
            images[path] = (width, height, digest(path))
        width, height, sha = images[path]
        if pinned and (frame["sha256"] != sha or frame["width"] != width or frame["height"] != height):
            raise ValueError("Frame input changed: " + str(path))
        rect = frame.get("rect", [0, 0, width, height])
        if (not isinstance(rect, list) or len(rect) != 4 or any(type(x) is not int for x in rect)
                or min(rect[:2]) < 0 or min(rect[2:]) < 1
                or rect[0] + rect[2] > width or rect[1] + rect[3] > height):
            raise ValueError("Frame rectangle is outside its PNG")
        result.append({"name": label, "path": str(path.relative_to(Path(workspace).resolve())),
                       "sha256": sha, "width": width, "height": height, "rect": rect})
    return result


def validate(workspace, receipt):
    path = inside(workspace, receipt)
    data = load(path)
    if (data.get("schema") != SCHEMA or data.get("version") != 1 or data.get("alpha") != "straight"
            or data.get("coordinate_origin") != "top_left" or type(data.get("loop")) is not bool):
        raise ValueError("Unsupported animation media receipt")
    name(data.get("name"))
    fps = rate(data.get("fps"))
    checked = frames(workspace, data.get("frames"), pinned=True)
    if len(checked) * fps["denominator"] / fps["numerator"] > 300:
        raise ValueError("Animation exceeds 300 seconds")
    sources = data.get("sources", [])
    if (not isinstance(sources, list) or len(sources) > 1066
            or any(not isinstance(item, dict) or set(item) != {"path", "sha256"} for item in sources)):
        raise ValueError("Invalid source fingerprints")
    for item in sources:
        if digest(inside(workspace, item["path"])) != item["sha256"]:
            raise ValueError("Source changed: " + item["path"])
    return {"status": "inputs_unchanged", "receipt": str(path), "sha256": digest(path),
            "frame_count": len(checked), "fps": fps, "launches_applications": False}


def ingest(workspace, recipe, directory):
    """Pin explicitly ordered PNG frames or atlas rectangles and editable source files."""
    source, target = inside(workspace, recipe), inside(workspace, directory)
    spec = load(source)
    if (not isinstance(spec, dict) or spec.get("version") != 1
            or set(spec) - {"version", "name", "fps", "loop", "alpha", "frames", "sources"}
            or spec.get("alpha") != "straight" or type(spec.get("loop")) is not bool):
        raise ValueError("Declare version 1, straight alpha, loop, FPS and ordered frames")
    fps, label = rate(spec.get("fps")), name(spec.get("name"))
    checked = frames(workspace, spec.get("frames"), pinned=False)
    if len(checked) * fps["denominator"] / fps["numerator"] > 300:
        raise ValueError("Animation exceeds 300 seconds")
    sources = spec.get("sources", [])
    if (not isinstance(sources, list) or len(sources) > 64
            or any(not isinstance(item, str) for item in sources)):
        raise ValueError("Declare at most 64 source paths")
    inputs = [source, *(inside(workspace, item) for item in sources)]
    refs = [{"path": str(item.relative_to(Path(workspace).resolve())), "sha256": digest(item)} for item in inputs]
    target.mkdir(parents=True, exist_ok=False)
    output = target / "media.json"
    write(output, {"schema": SCHEMA, "version": 1, "name": label, "fps": fps,
                   "loop": spec["loop"], "alpha": "straight", "coordinate_origin": "top_left",
                   "frames": checked, "sources": refs})
    return validate(workspace, str(output))


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
            if stream.getcomptype() != "NONE" or stream.getnchannels() not in (1, 2):
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
