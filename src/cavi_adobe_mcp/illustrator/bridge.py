"""Bounded Illustrator authoring on new, cooperatively owned AI copies."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from ..core.extendscript import AdobeOutcomeUnknown, OwnedCopyHost, _name, validate_edits


def _svg(path, *, native_export=False):
    import xml.etree.ElementTree as ET
    raw = path.read_bytes()
    # Native exports can include a public DTD; remove it without fetching external resources.
    if b"<!ENTITY" in raw.upper() or re.search(br"<!DOCTYPE[^>]*\[", raw, re.IGNORECASE):
        raise AdobeOutcomeUnknown("SVG contains an entity declaration")
    if b"<?xml-stylesheet" in raw.lower():
        raise AdobeOutcomeUnknown("SVG has an external stylesheet instruction")
    if re.search(br"<!DOCTYPE", raw, re.IGNORECASE):
        if not native_export:
            raise AdobeOutcomeUnknown("SVG source must not contain an external document type")
        raw = re.sub(br"<!DOCTYPE[^>]*>", b"", raw, flags=re.IGNORECASE)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise AdobeOutcomeUnknown("Native export is not valid SVG") from exc
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise AdobeOutcomeUnknown("Native export is not SVG")
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] in ("script", "foreignObject"):
            raise AdobeOutcomeUnknown("SVG contains executable or external document content")
        for key, value in element.attrib.items():
            local = key.rsplit("}", 1)[-1]
            if local.lower().startswith("on"):
                raise AdobeOutcomeUnknown("SVG has an event handler")
            if local == "href" and not value.startswith(("#", "data:")):
                raise AdobeOutcomeUnknown("SVG has an external dependency")
    if b"@import" in raw.lower():
        raise AdobeOutcomeUnknown("SVG has an external CSS dependency")
    for dependency in re.findall(br"url\(([^)]*)\)", raw, re.IGNORECASE):
        if not dependency.strip().strip(b"\"'").startswith((b"#", b"data:")):
            raise AdobeOutcomeUnknown("SVG has an external CSS dependency")
    if native_export:
        path.write_bytes(raw)
    return {"usage": "color", "srgb": True, "self_contained": True}


class IllustratorAuthoring(OwnedCopyHost):
    app = "illustrator"
    BUNDLE_ID = "com.adobe.illustrator"
    APP_PATTERN = "Adobe Illustrator*/*.app"
    APP_ENV = "CAVI_ILLUSTRATOR_APP"
    APP_NAME = "Adobe Illustrator"
    SUFFIX = ".ai"
    SOURCES = frozenset({".ai", ".svg", ".png"})
    DRIVER = Path(__file__).with_name("driver.jsx")

    def set_text_font(self, session_id, layer_path, expected_name, frame_index, font):
        """Give one text frame of an exact layer an exact font name."""
        validate_edits([{"path": layer_path, "expected_name": expected_name, "visible": True}])
        if type(frame_index) is not int or not 0 <= frame_index <= 10000:
            raise ValueError("frame_index must be an integer in 0..10000")
        _name(font)
        return self._owned_action(session_id, "set_text_font", {
            "layer_path": layer_path, "expected_name": expected_name,
            "frame_index": frame_index, "font": font})

    def _check_source(self, path):
        if path.suffix.lower() == ".svg":
            _svg(path)

    def _interpret(self, kind, staged, usage, result):
        if kind == "svg":
            return _svg(staged, native_export=True)
        return super()._interpret(kind, staged, usage, result)

    def export(self, session_id: str, destination: str, *, kind: Literal["png", "svg"] = "svg",
               usage: Literal["color", "data"] = "color"):
        """Export an SVG (default) or a PNG24 of the document's single artboard."""
        if kind not in ("png", "svg") or usage not in ("color", "data"):
            raise ValueError("Choose png/svg and color/data explicitly")
        if kind == "svg" and usage != "color":
            raise ValueError("SVG export requires color usage")
        return self._export(session_id, destination, kind, usage)
