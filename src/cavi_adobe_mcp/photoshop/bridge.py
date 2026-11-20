"""Bounded Photoshop authoring on new, cooperatively owned PSD copies."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from cavi_mcp_core.media import digest

from ..core.extendscript import AdobeOutcomeUnknown, OwnedCopyHost, _name, _number, validate_edits
from .fontlist import classify_font_file, font_list_location, read_font_list

MAX_TEXT_LAYERS = 2048
MAX_TEXT_RUNS = 4096


class PhotoshopAuthoring(OwnedCopyHost):
    app = "photoshop"
    BUNDLE_ID = "com.adobe.Photoshop"
    APP_PATTERN = "Adobe Photoshop*/*.app"
    APP_ENV = "CAVI_PHOTOSHOP_APP"
    APP_NAME = "Adobe Photoshop"
    SUFFIX = ".psd"
    SOURCES = frozenset({".psd", ".png"})
    DRIVER = Path(__file__).with_name("driver.jsx")
    INITIAL_ACTIONS = ("open_copy", "create_canvas")

    @staticmethod
    def _home():
        return Path.home()

    def fonts(self, family=None):
        """Native fonts with the kind of font each is, read from Photoshop's font list."""
        if family is not None and (not isinstance(family, str) or not 1 <= len(family) <= 128
                                   or any(ord(c) < 32 for c in family)):
            raise ValueError("family must contain 1..128 printable characters")
        result = self._read("fonts")
        application = result["runtime"]["application"]
        home = self._home()
        location = font_list_location(application, home)
        listing = read_font_list(location) if location else None
        records, modified = listing if listing else ({}, None)
        fonts = []
        for font in result["fonts"]:
            if family is not None and font["family"].casefold() != family.casefold():
                continue
            file = records.get(font["postscript_name"])
            fonts.append({**font, "file": file,
                          "source": classify_font_file(file, home=home, application=application)})
        return {**result, "fonts": fonts,
                "font_list": {"path": str(location), "modified": modified} if listing else None}

    def text_fonts(self, session_id):
        """Every text layer's style runs with the PostScript font each uses and whether Photoshop has it."""
        packet = self._session(session_id)
        result = self._read("text_fonts", packet)
        layers = result.get("layers")
        if not isinstance(layers, list) or len(layers) > MAX_TEXT_LAYERS:
            raise ValueError("Native text font receipt exceeds the 2048-layer limit")
        runs = sum(len(layer["runs"]) for layer in layers)
        if runs > MAX_TEXT_RUNS:
            raise ValueError("Document exceeds the 4096-run text font limit")
        missing = sorted({run["postscript_name"] for layer in layers for run in layer["runs"]
                          if run["available"] is False and isinstance(run["postscript_name"], str)})
        return {**result, "missing_fonts": missing}

    def set_text_font(self, session_id, layer_path, expected_name, font):
        """Give every run of one exact text layer an exact PostScript font."""
        validate_edits([{"path": layer_path, "expected_name": expected_name, "visible": True}])
        _name(font)
        return self._owned_action(session_id, "set_text_font", {
            "layer_path": layer_path, "expected_name": expected_name, "font": font})

    def _verify(self, action, payload, result):
        if action != "create_canvas":
            return
        expected = {"width": payload["canvas_width"], "height": payload["canvas_height"],
                    "color_space": "DocumentMode.RGB",
                    "bit_depth": "BitsPerChannelType." + (
                        "SIXTEEN" if payload["canvas_bit_depth"] == 16 else "EIGHT"),
                    "color_profile": "sRGB IEC61966-2.1"}
        if any(result.get(key) != value for key, value in expected.items()):
            raise AdobeOutcomeUnknown("Native canvas does not match the requested RGB sRGB dimensions; inspect app and copy")

    def _release_reservation(self, action, packet):
        if action == "create_canvas":
            self._remove_canvas_reservation(packet)

    @staticmethod
    def _remove_canvas_reservation(packet):
        """Remove only the unchanged regular-file reservation created for this canvas."""
        path = Path(packet["copy"])
        try:
            expected = packet["reservation_identity"]
            before = path.lstat()
            if (not stat.S_ISREG(before.st_mode)
                    or {"device": before.st_dev, "inode": before.st_ino} != expected):
                return
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                opened = os.fstat(descriptor)
                if (not stat.S_ISREG(opened.st_mode)
                        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                    return
                contents = os.read(descriptor, 128)
            finally:
                os.close(descriptor)
            after = path.lstat()
            if (contents != ("CAVI_ADOBE_RESERVED:" + packet["destination_reservation"]).encode()
                    or not stat.S_ISREG(after.st_mode)
                    or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)):
                return
            path.unlink()
        except (OSError, KeyError, TypeError):
            # Preservation is safer if the reservation cannot be proven unchanged.
            return

    def create_canvas(self, width: int, height: int, destination: str, bit_depth: int = 16):
        """Create a new transparent sRGB Photoshop canvas, defaulting to 16-bit samples."""
        if type(width) is not int or not 1 <= width <= 2880:
            raise ValueError("width must be an integer in 1..2880")
        if type(height) is not int or not 1 <= height <= 2880:
            raise ValueError("height must be an integer in 1..2880")
        if type(bit_depth) is not int or bit_depth not in (8, 16):
            raise ValueError("bit_depth must be 8 or 16")
        target = self._path(destination, suffix={".psd"})
        if not target.parent.is_dir():
            raise ValueError("Canvas destination directory must already exist")
        recipe = {"version": 1, "kind": "photoshop_canvas_recipe", "width": width,
                  "height": height, "resolution": 72, "mode": "RGB", "bit_depth": bit_depth,
                  "background": "transparent", "color_profile": "sRGB IEC61966-2.1"}
        with self._locked() as pending:
            state = self._runtime()
            if pending.exists():
                raise AdobeOutcomeUnknown("Inspect the earlier pending Adobe command before creating a canvas")
            self._store()
            lineage = Path(tempfile.mkdtemp(prefix="canvas-recipe-", dir=self.store))
            recipe_path = lineage / "canvas-recipe.json"
            input_copy = lineage / "input" / "canvas-recipe.json"
            input_copy.parent.mkdir(mode=0o700)
            encoded = json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode("utf-8")
            with recipe_path.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            recipe_path.chmod(0o400)
            with input_copy.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            input_copy.chmod(0o400)
            source_hash = digest(recipe_path)
            if digest(input_copy) != source_hash:
                raise ValueError("Canvas recipe changed while staging its private input copy")
            reservation = uuid.uuid4().hex
            with target.open("x") as stream:
                stream.write("CAVI_ADOBE_RESERVED:" + reservation)
                stream.flush()
                os.fsync(stream.fileno())
            reservation_info = target.lstat()
            packet = {"session_id": uuid.uuid4().hex, "app": self.app,
                      "workspace": str(self.workspace), "source": str(recipe_path),
                      "source_sha256": source_hash, "copy": str(target),
                      "copy_sha256": source_hash, "input_copy": str(input_copy),
                      "process": state["process"], "state": "created",
                      "ownership_token": uuid.uuid4().hex,
                      "destination_reservation": reservation,
                      "reservation_identity": {"device": reservation_info.st_dev,
                                               "inode": reservation_info.st_ino},
                      "source_kind": "canvas_recipe"}
            self._record(packet)

            def receipt(response):
                return {**response, "source_kind": "canvas_recipe",
                        "source_sha256_kind": "canvas_recipe_json",
                        "canvas_recipe": self._artifact(recipe_path)}

            return self._mutate(packet, "create_canvas",
                                {"canvas_width": width, "canvas_height": height, "canvas_bit_depth": bit_depth},
                                pending, state, receipt)

    def resize_image(self, session_id: str, width: int, height: int):
        """Resample an owned Photoshop document while retaining its editable contents."""
        for label, value in (("width", width), ("height", height)):
            if type(value) is not int or not 1 <= value <= 2880:
                raise ValueError(f"{label} must be an integer in 1..2880")
        return self._owned_action(session_id, "resize_image", {
            "resize_width": width, "resize_height": height})

    def export(self, session_id: str, destination: str, *, usage: Literal["color", "data"] = "color"):
        """Export a PNG at the document's native bit depth."""
        if usage not in ("color", "data"):
            raise ValueError("Choose color/data explicitly")
        return self._export(session_id, destination, "png", usage)

    def generative_fill(self, session_id, prompt, bounds=None):
        """Generate a new layer from a prompt inside bounds, or across the canvas without bounds."""
        if (not isinstance(prompt, str) or len(prompt) > 1000
                or any(ord(c) < 32 for c in prompt)):
            raise ValueError("prompt must contain 0..1000 printable characters")
        if bounds is not None:
            if not isinstance(bounds, list) or len(bounds) != 4:
                raise ValueError("bounds must be x,y,width,height")
            for index, value in enumerate(bounds):
                _number(value, "bounds", 0 if index < 2 else 1, 30000)
        return self._owned_action(session_id, "generative_fill", {"prompt": prompt, "bounds": bounds},
                                  self._generative("photoshop_generative_fill", prompt, bounds))

    @staticmethod
    def _generative(tool, prompt, bounds):
        def finalize(response):
            layers = response["document"].get("changed_layers", [])
            return {**response, "generative": {
                "tool": tool, "service": "Adobe Firefly in Photoshop",
                "prompt": prompt, "bounds": bounds, "layers": layers}}
        return finalize

    def remove_background(self, session_id, layer_path, expected_name):
        """Mask everything except the detected subject on one exact pixel layer."""
        validate_edits([{"path": layer_path, "expected_name": expected_name, "visible": True}])
        return self._owned_action(session_id, "remove_background", {
            "layer_path": layer_path, "expected_name": expected_name})

    def generative_upscale(self, session_id):
        """Double the pixel dimensions and add the upscaled image as a new top layer."""
        return self._owned_action(session_id, "generative_upscale", {},
                                  self._generative("photoshop_generative_upscale", None, None))
