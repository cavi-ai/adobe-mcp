"""Bounded Photoshop and Illustrator authoring on new, cooperatively owned copies.

The public methods accept data, never caller-supplied JavaScript. Native commands
run only in an already running app; this module never launches or signs into one.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import uuid
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from cavi_mcp_core.journal import archive, completion, fingerprint
from cavi_mcp_core.journal import write_json as _write_json

App = Literal["photoshop", "illustrator"]
APPS = {
    "photoshop": ("com.adobe.Photoshop", "Adobe Photoshop*/*.app", ".psd"),
    "illustrator": ("com.adobe.illustrator", "Adobe Illustrator*/*.app", ".ai"),
}
DRIVER = Path(__file__).with_name("adobe_authoring.jsx")
SESSION = re.compile(r"^[0-9a-f]{32}$")
SNAPSHOT_FIELDS = ("name", "path", "native_id", "identity", "saved", "width", "height",
                   "units", "color_profile", "color_space", "bit_depth", "layers", "paths")


class AdobeCommandRejected(ValueError):
    """Native preflight rejected a command before any document mutation."""


class AdobeOutcomeUnknown(RuntimeError):
    """A command may have executed; inspect the app and outputs, never retry it."""


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _number(value, name, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a finite number in {low}..{high}")
    return value


def _native_snapshot(result):
    if not isinstance(result, dict) or any(key not in result for key in SNAPSHOT_FIELDS):
        raise AdobeOutcomeUnknown("Native document receipt has no complete ownership snapshot")
    return {key: result[key] for key in SNAPSHOT_FIELDS}


def _name(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 128
            or any(ord(c) < 32 for c in value)):
        raise ValueError("Layer names must contain 1..128 printable characters")
    return value


def validate_edits(edits):
    if not isinstance(edits, list) or not 1 <= len(edits) <= 64:
        raise ValueError("Provide 1..64 layer edits")
    result = []
    seen = set()
    for edit in edits:
        if (not isinstance(edit, dict) or not {"path", "expected_name"} <= set(edit)
                or set(edit) - {"path", "expected_name", "name", "visible", "opacity"}
                or len(edit) < 3):
            raise ValueError("Layer edits require path, expected_name and name/visible/opacity")
        path = edit["path"]
        if (not isinstance(path, list) or not 1 <= len(path) <= 32
                or any(type(i) is not int or not 0 <= i <= 10000 for i in path)
                or tuple(path) in seen):
            raise ValueError("Layer paths must be unique lists of bounded zero-based indices")
        seen.add(tuple(path))
        _name(edit["expected_name"])
        if "name" in edit:
            _name(edit["name"])
        if "visible" in edit and type(edit["visible"]) is not bool:
            raise ValueError("visible must be a boolean")
        if "opacity" in edit:
            _number(edit["opacity"], "opacity", 0, 100)
        result.append(dict(edit))
    return result


def _png(path: Path, *, data=False, color_profile=None, inspect_only=False):
    """Check a native PNG and, for data maps, remove only color interpretation chunks."""
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise AdobeOutcomeUnknown("Native export is not a PNG")
    offset, chunks, color, dimensions = 8, [], [], None
    ended = False
    while offset + 12 <= len(raw):
        size = struct.unpack(">I", raw[offset:offset + 4])[0]
        end = offset + size + 12
        if end > len(raw):
            raise AdobeOutcomeUnknown("Native PNG is truncated")
        kind = raw[offset + 4:offset + 8]
        payload = raw[offset + 8:end - 4]
        if zlib.crc32(kind + payload) & 0xffffffff != struct.unpack(">I", raw[end - 4:end])[0]:
            raise AdobeOutcomeUnknown("Native PNG has an invalid checksum")
        if kind == b"IHDR":
            if dimensions is not None or size != 13 or chunks:
                raise AdobeOutcomeUnknown("Native PNG has an invalid header")
            width, height, depth, mode, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload)
            if (not width or not height or depth not in (8, 16) or mode not in (0, 2, 4, 6)
                    or compression or filtering or interlace not in (0, 1)):
                raise AdobeOutcomeUnknown("Native export must be an 8/16-bit RGB or grayscale PNG")
            dimensions = {"width": width, "height": height, "bit_depth": depth}
        if kind in (b"iCCP", b"sRGB", b"gAMA", b"cHRM"):
            color.append(kind.decode("ascii"))
        if not data or kind not in (b"iCCP", b"sRGB", b"gAMA", b"cHRM"):
            chunks.append(raw[offset:end])
        offset = end
        if kind == b"IEND":
            ended = True
            break
    if (dimensions is None or not ended or offset != len(raw)
            or not any(chunk[4:8] == b"IDAT" for chunk in chunks)):
        raise AdobeOutcomeUnknown("Native PNG is incomplete")
    if inspect_only:
        return {**dimensions, "color_chunks": color}
    if data:
        path.write_bytes(raw[:8] + b"".join(chunks))
    elif not any(item in color for item in ("iCCP", "sRGB")):
        if color_profile != "sRGB IEC61966-2.1":
            raise AdobeOutcomeUnknown("Color PNG requires a confirmed sRGB document profile")
        payload = b"sRGB\x00"
        declaration = struct.pack(">I", 1) + payload + struct.pack(">I", zlib.crc32(payload))
        # Add interpretation metadata only; the native compressed pixel stream stays unchanged.
        path.write_bytes(raw[:33] + declaration + raw[33:])
    return {**dimensions, "removed_color_chunks": color if data else [],
            "srgb": not data, "usage": "data" if data else "color"}


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


class AdobeAuthoring:
    def __init__(self, workspace, app: App, *, timeout=45, app_path=None):
        if app not in APPS:
            raise ValueError("app must be photoshop or illustrator")
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("Workspace must be a directory")
        self.app = app
        self.timeout = _number(timeout, "timeout", 1, 120)
        self.app_path = Path(app_path).resolve() if app_path else None
        self.store = self.workspace / ".adobe-authoring"

    def _path(self, value, *, suffix=None, exists=False):
        if (not isinstance(value, str) or not value or ".." in Path(value).parts
                or any(ord(c) < 32 for c in value)):
            raise ValueError("Use a workspace path without parent traversal or control characters")
        logical = self.workspace / value
        path = logical.resolve()
        if (not path.is_relative_to(self.workspace) or path == self.workspace
                or logical != path):
            raise ValueError("Paths must remain inside the workspace without symlinks")
        if suffix and path.suffix.lower() not in suffix:
            raise ValueError("Unsupported file extension")
        if exists:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Input must be a regular file")
        elif path.exists():
            raise ValueError("Destination already exists")
        return path

    def _application(self):
        if sys.platform != "darwin":
            raise RuntimeError("Native Adobe authoring requires macOS")
        candidates = [self.app_path] if self.app_path else sorted(
            Path("/Applications").glob(APPS[self.app][1]), reverse=True)
        for candidate in candidates:
            try:
                info = plistlib.loads((candidate / "Contents/Info.plist").read_bytes())
            except (FileNotFoundError, plistlib.InvalidFileException):
                continue
            if info.get("CFBundleIdentifier") == APPS[self.app][0]:
                executable = candidate / "Contents/MacOS" / info["CFBundleExecutable"]
                return candidate, info, executable
        raise FileNotFoundError(f"{self.app} is not installed")

    def status(self):
        """Inspect installation and process identity without sending Apple events or launching apps."""
        path, info, executable = self._application()
        process = subprocess.run(["/bin/ps", "-axo", "pid=,lstart=,comm="],
                                 capture_output=True, text=True, check=True, timeout=5)
        matches = []
        for line in process.stdout.splitlines():
            parts = line.strip().split(maxsplit=6)
            if len(parts) == 7 and parts[6] == str(executable):
                matches.append({"pid": int(parts[0]), "started": " ".join(parts[1:6])})
        if len(matches) > 1:
            raise RuntimeError("Multiple app processes found; select a single instance before authoring")
        return {"app": self.app, "application": str(path), "version": info["CFBundleShortVersionString"],
                "running": bool(matches), "process": matches[0] if matches else None,
                "launches_application": False, "authenticates": False}

    def _runtime(self):
        state = self.status()
        if not state["running"]:
            raise RuntimeError("App is not running; open it manually and resolve any prompts first")
        return state

    @contextmanager
    def _locked(self):
        # The global cooperative lock covers this app across workspaces and Python processes.
        location = Path(tempfile.gettempdir()) / f"cavi-adobe-{os.getuid()}-{self.app}"
        location.mkdir(mode=0o700, exist_ok=True)
        if location.is_symlink() or location.stat().st_uid != os.getuid():
            raise ValueError("Unsafe Adobe lock directory")
        with (location / "lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Another Adobe authoring operation is active") from exc
            try:
                yield location / "pending.json"
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _store(self):
        self.store.mkdir(mode=0o700, exist_ok=True)
        if self.store.is_symlink():
            raise ValueError("Adobe session directory must not be a symlink")

    def _record(self, packet):
        self._store()
        path = self.store / f"{packet['session_id']}.json"
        _write_json(path, packet)

    def _session(self, session_id):
        if not isinstance(session_id, str) or not SESSION.fullmatch(session_id):
            raise ValueError("Invalid Adobe session id")
        path = self.store / f"{session_id}.json"
        if path.is_symlink() or self.store.is_symlink():
            raise ValueError("Adobe session records must not be symlinks")
        packet = json.loads(path.read_text())
        if (packet["app"] != self.app or packet["workspace"] != str(self.workspace)
                or packet["session_id"] != session_id
                or not SESSION.fullmatch(packet.get("ownership_token", ""))):
            raise ValueError("Session belongs to a different app or workspace")
        self._path(packet["source"], exists=True)
        self._path(packet["copy"], suffix={APPS[self.app][2]}, exists=Path(packet["copy"]).exists())
        self._path(packet["input_copy"], exists=True)
        for key in ("source_sha256", "copy_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", packet[key]):
                raise ValueError("Invalid session artifact digest")
        return packet

    def _native(self, request, state, directory):
        """Transport only the fixed repository driver plus JSON-encoded data."""
        script = directory / f"{uuid.uuid4().hex}.jsx"
        request = {**request, "completion_path": str(directory / "completion.json")}
        script_text = ('#targetengine "cavi_adobe_authoring"\nvar CAVI_REQUEST = '
                       + json.dumps(request, ensure_ascii=True, allow_nan=False)
                       + ";\n" + DRIVER.read_text())
        script.write_text(script_text)
        # using terms only compiles against the installed dictionary. The running check does not launch.
        app_literal = json.dumps(state["application"])
        bridge = ("on run argv\nusing terms from application " + app_literal
                  + "\nif not (application id \"" + APPS[self.app][0] + "\" is running) then "
                  + 'error "Application stopped"\ntell application id "' + APPS[self.app][0]
                  + '" to do javascript (item 2 of argv)\nend using terms from\nend run')
        try:
            result = subprocess.run(["/usr/bin/osascript", "-e", bridge, str(script), script_text],
                                    capture_output=True, text=True, timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise AdobeOutcomeUnknown("Adobe command timed out; inspect app, prompts and outputs") from exc
        if result.returncode:
            raise AdobeOutcomeUnknown("Adobe command failed; inspect app and authorization prompts: "
                                      + result.stderr.strip()[:1000])
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AdobeOutcomeUnknown("Adobe returned no valid command receipt") from exc
        if self._runtime()["process"] != state["process"]:
            raise AdobeOutcomeUnknown("App process identity changed during the command")
        if not isinstance(response, dict) or response.get("operation_id") != request["operation_id"]:
            raise AdobeOutcomeUnknown("Adobe receipt identity does not match the command")
        if response.get("ok") is not True:
            error = response.get("error", "Native command rejected")
            if response.get("outcome") == "rejected":
                raise AdobeCommandRejected(error)
            raise AdobeOutcomeUnknown(error)
        return response["result"]

    def _read(self, action, packet=None):
        with self._locked() as pending:
            state = self._runtime()
            if packet and packet["process"] != state["process"]:
                raise ValueError("Session app process changed; ownership is no longer valid")
            directory = Path(tempfile.mkdtemp(prefix="cavi-adobe-inspect-"))
            request = {"action": action, "app": self.app, "operation_id": uuid.uuid4().hex,
                       "session_id": packet["session_id"] if packet else None,
                       "document_path": packet["copy"] if packet else None,
                       "ownership_token": packet["ownership_token"] if packet else None,
                       "expected_document": packet.get("native_snapshot") if packet else None}
            try:
                result = self._native(request, state, directory)
            except AdobeOutcomeUnknown:
                if not pending.exists():
                    _write_json(pending, {"action": action, "state": "unknown",
                        "operation_id": request["operation_id"], "workspace": str(self.workspace),
                        "process": state["process"], "diagnostics": str(directory)})
                raise
            else:
                shutil.rmtree(directory)
                self._reconcile_read_marker(pending, action, state, request)
            return {**result, "runtime": state,
                    "pending_operation": json.loads(pending.read_text()) if pending.exists() else None}

    def _reconcile_read_marker(self, pending, action, state, request):
        """Resolve only a same-process unknown inspect after a later successful inspect."""
        if action != "inspect" or not pending.exists():
            return
        prior = json.loads(pending.read_text())
        if (prior.get("action") != "inspect" or prior.get("state") != "unknown"
                or prior.get("workspace") != str(self.workspace)
                or prior.get("process") != state["process"]
                or not isinstance(prior.get("operation_id"), str)
                or not SESSION.fullmatch(prior["operation_id"])):
            return
        self._store()
        archive = self.store / f"reconciled-read-{prior['operation_id']}.json"
        record = {"resolution": "read_only_reconciled", "prior": prior,
                  "reconciled_by_operation_id": request["operation_id"]}
        if archive.exists():
            try:
                saved = json.loads(archive.read_text())
            except (OSError, json.JSONDecodeError):
                return
            if (saved.get("resolution") != "read_only_reconciled"
                    or saved.get("prior") != prior):
                return
        else:
            _write_json(archive, record)
        pending.unlink()

    def inspect(self):
        """Read open documents and layers; restore the active document without saving source documents."""
        return self._read("inspect")

    def inspect_session(self, session_id):
        packet = self._session(session_id)
        return {"session": {k: v for k, v in packet.items() if k not in ("ownership_token", "destination_reservation")},
                "native": self._read("inspect_session", packet)}

    def _mutate(self, packet, action, payload, pending, state, finalize=None):
        if pending.exists():
            raise AdobeOutcomeUnknown("An earlier Adobe command has no resolved outcome; inspect app and "
                                      + str(pending))
        initial_action = action in ("open_copy", "create_canvas")
        if packet["state"] != "owned" and not initial_action:
            raise AdobeOutcomeUnknown("Session is not available for mutations; inspect it first")
        if packet["process"] != state["process"]:
            raise ValueError("Session app process changed; ownership is no longer valid")
        if digest(Path(packet["source"])) != packet["source_sha256"]:
            raise ValueError("Source changed since this copy was created")
        checked_copy = Path(packet["input_copy"] if initial_action else packet["copy"])
        if digest(checked_copy) != packet["copy_sha256"]:
            raise ValueError("Working copy changed outside this session")
        operation_id = uuid.uuid4().hex
        record = {"operation_id": operation_id, "action": action, "session_id": packet["session_id"],
                  "workspace": str(self.workspace), "process": state["process"], "state": "pending"}
        _write_json(pending, record)
        packet["state"] = "pending"
        packet["last_operation"] = record
        self._record(packet)
        native_rejected = False
        try:
            directory = Path(tempfile.mkdtemp(prefix="cavi-adobe-command-"))
            record["diagnostics"] = str(directory)
            _write_json(pending, record)
            try:
                result = self._native({**payload, "action": action, "app": self.app,
                    "operation_id": operation_id, "session_id": packet["session_id"],
                    "document_path": packet["copy"], "ownership_token": packet["ownership_token"],
                    "expected_document": packet.get("native_snapshot"),
                    "input_copy": packet.get("input_copy"),
                    "destination_reservation": packet.get("destination_reservation"),
                    **({"canvas_width": payload["canvas_width"],
                       "canvas_height": payload["canvas_height"],
                       "canvas_bit_depth": payload["canvas_bit_depth"]}
                       if action == "create_canvas" else {})}, state, directory)
            except AdobeCommandRejected:
                native_rejected = True
                raise
            if digest(Path(packet["source"])) != packet["source_sha256"]:
                raise AdobeOutcomeUnknown("Source changed during the command; inspect source and app")
            if action == "create_canvas":
                expected = {"width": payload["canvas_width"], "height": payload["canvas_height"],
                            "color_space": "DocumentMode.RGB", "bit_depth": "BitsPerChannelType." + ("SIXTEEN" if payload["canvas_bit_depth"] == 16 else "EIGHT"),
                            "color_profile": "sRGB IEC61966-2.1"}
                if any(result.get(key) != value for key, value in expected.items()):
                    raise AdobeOutcomeUnknown("Native canvas does not match the requested RGB sRGB dimensions; inspect app and copy")
            packet["copy_sha256"] = digest(Path(packet["copy"]))
            if action == "close_copy":
                packet.pop("native_snapshot", None)
            else:
                packet["native_snapshot"] = _native_snapshot(result)
            packet["state"] = "closed" if action == "close_copy" else "owned"
            packet["last_operation"] = {**record, "state": "observed", "result": result}
            response = {"session_id": packet["session_id"], "operation_id": operation_id,
                        "document": result, "copy": self._artifact(Path(packet["copy"])),
                        "source_sha256": packet["source_sha256"], "retry_safe": False}
            if finalize:
                response = finalize(response)
            self._record(packet)
            shutil.rmtree(directory)
            pending.unlink()
            return response
        except BaseException as error:
            if native_rejected:
                # Only the native driver's pre-mutation rejection permits this classification.
                if action == "create_canvas":
                    self._remove_canvas_reservation(packet)
                packet["state"] = "rejected" if action in ("open_copy", "create_canvas") else "owned"
                packet["last_operation"] = {**record, "state": "rejected"}
                self._record(packet)
                pending.unlink()
                shutil.rmtree(directory)
                raise
            packet["state"] = "unknown"
            try:
                self._record(packet)
            except Exception as journal_error:
                if isinstance(error, Exception):
                    raise AdobeOutcomeUnknown(
                        "Adobe outcome is unknown and its session could not be recorded; "
                        "inspect the app, pending journal and outputs") from error
                error.add_note("Adobe session could not be recorded; inspect its pending journal")
                raise error from journal_error
            # Preserve cancellation and interrupts, but never expose a post-dispatch input error.
            if isinstance(error, AdobeOutcomeUnknown) or not isinstance(error, Exception):
                raise
            raise AdobeOutcomeUnknown(
                "Adobe command outcome is unknown; inspect the app, pending journal and outputs") from error

    def _artifact(self, path):
        if not path.is_file() or path.stat().st_size == 0:
            raise AdobeOutcomeUnknown("Native output is missing or empty")
        return {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size}

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

    def open_copy(self, source: str, destination: str):
        """Open a private byte-for-byte source copy, then save a new PSD/AI, never the source.

        Unsaved changes in any already open source are deliberately not captured.
        """
        allowed = {".psd", ".png"} if self.app == "photoshop" else {".ai", ".svg", ".png"}
        source_path = self._path(source, suffix=allowed, exists=True)
        if source_path.suffix.lower() == ".svg":
            _svg(source_path)
        target = self._path(destination, suffix={APPS[self.app][2]})
        if not target.parent.is_dir():
            raise ValueError("Copy destination directory must already exist")
        with self._locked() as pending:
            state = self._runtime()
            if pending.exists():
                raise AdobeOutcomeUnknown("Inspect the earlier pending Adobe command before opening a copy")
            source_hash = digest(source_path)
            self._store()
            directory = Path(tempfile.mkdtemp(prefix="source-", dir=self.store))
            input_copy = directory / ("source" + source_path.suffix.lower())
            with source_path.open("rb") as src, input_copy.open("xb") as dst:
                shutil.copyfileobj(src, dst)
            if digest(input_copy) != source_hash or digest(source_path) != source_hash:
                raise ValueError("Source changed while creating its copy")
            reservation = uuid.uuid4().hex
            with target.open("x") as placeholder:
                placeholder.write("CAVI_ADOBE_RESERVED:" + reservation)
            packet = {"session_id": uuid.uuid4().hex, "app": self.app, "workspace": str(self.workspace),
                      "source": str(source_path), "source_sha256": source_hash, "copy": str(target),
                      "copy_sha256": source_hash, "input_copy": str(input_copy),
                      "process": state["process"], "state": "created",
                      "ownership_token": uuid.uuid4().hex, "destination_reservation": reservation}
            self._record(packet)
            return self._mutate(packet, "open_copy", {}, pending, state)

    def create_canvas(self, width: int, height: int, destination: str, bit_depth: int = 16):
        """Create a new transparent sRGB Photoshop canvas, defaulting to 16-bit samples."""
        if self.app != "photoshop":
            raise ValueError("Canvas creation is supported only in Photoshop")
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

    def _owned_action(self, session_id, action, payload, finalize=None):
        with self._locked() as pending:
            packet = self._session(session_id)
            return self._mutate(packet, action, payload, pending, self._runtime(), finalize)

    def operation_control(self, action, session_id, operation_id, expected_copy_sha256=None,
                          expected_native_sha256=None):
        if action not in ("inspect", "adopt_current", "abandon_after_exit"):
            raise ValueError("Choose inspect, adopt_current or abandon_after_exit")
        if not isinstance(operation_id, str) or not SESSION.fullmatch(operation_id):
            raise ValueError("Invalid operation ID")
        with self._locked() as pending:
            packet = self._session(session_id)
            record = json.loads(pending.read_text())
            if (record.get("operation_id") != operation_id or record.get("session_id") != session_id
                    or record.get("workspace") != str(self.workspace)):
                raise ValueError("No matching unresolved operation in this workspace")
            state = self.status()
            same_process = state.get("running") and state.get("process") == record.get("process")
            if action == "abandon_after_exit":
                if same_process:
                    raise ValueError("The original application process is still running")
                resolution = {"state": "abandoned_after_process_exit", "operation_id": operation_id,
                              "session_id": session_id, "retry_safe": False}
                self._store()
                archive(self.store / ("recovered-" + operation_id + ".json"), record, resolution)
                packet["state"] = "abandoned"
                self._record(packet)
                pending.unlink()
                return resolution
            directory = Path(record.get("diagnostics", ""))
            if (directory.is_symlink() or directory.resolve().parent != Path(tempfile.gettempdir()).resolve()
                    or not directory.name.startswith("cavi-adobe-command-")):
                raise ValueError("Operation has no safe retained native receipt directory")
            receipt = completion(directory / "completion.json", operation_id)
            source_ok = digest(Path(packet["source"])) == packet["source_sha256"]
            copy_hash = digest(Path(packet["copy"]))
            observation = None
            expected = packet.get("native_snapshot")
            if expected is None and receipt and receipt.get("ok"):
                expected = _native_snapshot(receipt.get("result"))
            closed = bool(receipt and receipt.get("ok") and receipt.get("result", {}).get("closed"))
            if closed and same_process and source_ok:
                with tempfile.TemporaryDirectory(prefix="cavi-adobe-recovery-") as folder:
                    observed = self._native({"action": "inspect", "app": self.app,
                        "operation_id": uuid.uuid4().hex}, state, Path(folder))
                if not any(doc.get("path") == packet["copy"] for doc in observed.get("documents", [])):
                    observation = {"closed": True, "path": packet["copy"]}
            elif receipt is not None and same_process and source_ok and expected is not None:
                with tempfile.TemporaryDirectory(prefix="cavi-adobe-recovery-") as folder:
                    request = {"action": "inspect_recovery", "app": self.app,
                        "operation_id": uuid.uuid4().hex, "session_id": session_id,
                        "document_path": packet["copy"], "ownership_token": packet["ownership_token"],
                        "expected_document": expected}
                    observation = _native_snapshot(self._native(request, state, Path(folder)))
            native_hash = fingerprint(observation) if observation is not None else None
            can_reconcile = (receipt is not None and bool(same_process) and source_ok
                             and observation is not None and (observation.get("saved") is True or observation.get("closed") is True))
            result = {"state": "unresolved", "operation_id": operation_id, "session_id": session_id,
                      "completion_available": receipt is not None, "can_reconcile": can_reconcile,
                      "source_unchanged": source_ok, "copy_sha256": copy_hash,
                      "native_sha256": native_hash, "document": observation, "retry_safe": False}
            if action == "inspect":
                return result
            if not can_reconcile:
                raise ValueError("Recovery requires a completed command and the same saved owned document")
            if expected_copy_sha256 != copy_hash or expected_native_sha256 != native_hash:
                raise ValueError("Copy or native observation changed; inspect recovery again")
            if not closed:
                with tempfile.TemporaryDirectory(prefix="cavi-adobe-recovery-") as folder:
                    adopted = self._native({"action": "adopt_recovery", "app": self.app,
                        "operation_id": uuid.uuid4().hex, "session_id": session_id,
                        "document_path": packet["copy"], "ownership_token": packet["ownership_token"],
                        "expected_document": expected, "expected_recovery_snapshot": observation}, state, Path(folder))
                if _native_snapshot(adopted) != observation:
                    raise AdobeOutcomeUnknown("Native recovery observation changed during adoption")
            if (digest(Path(packet["copy"])) != copy_hash
                    or digest(Path(packet["source"])) != packet["source_sha256"]):
                raise AdobeOutcomeUnknown("Recovery artifacts changed during adoption; inspect again")
            result.update(state="adopted_current")
            self._store()
            archive(self.store / ("recovered-" + operation_id + ".json"), record, result)
            packet.update(state="closed" if closed else "owned", copy_sha256=copy_hash, native_snapshot=observation,
                          last_operation={**record, "resolution": "adopted_current"})
            self._record(packet)
            pending.unlink()
            return result

    def edit_layers(self, session_id: str, edits: list[dict]):
        return self._owned_action(session_id, "edit_layers", {"edits": validate_edits(edits)})

    def add_rectangle(self, session_id: str, name: str, bounds: list, rgb: list,
                      opacity: float = 100):
        """Add a new filled motif/mask layer. Bounds are x,y,width,height in px (PS) or pt (AI)."""
        _name(name)
        if not isinstance(bounds, list) or len(bounds) != 4:
            raise ValueError("bounds must be x,y,width,height")
        for index, value in enumerate(bounds):
            _number(value, "bounds", 0 if index < 2 else .01, 30000)
        if not isinstance(rgb, list) or len(rgb) != 3:
            raise ValueError("rgb must contain three channel values")
        for value in rgb:
            _number(value, "rgb", 0, 255)
        _number(opacity, "opacity", 0, 100)
        return self._owned_action(session_id, "add_rectangle", {
            "name": name, "bounds": bounds, "rgb": rgb, "opacity": opacity})

    def add_group(self, session_id: str, name: str, *, parent_path=None, expected_parent_name=None):
        """Add a Photoshop layer set or Illustrator sublayer, optionally inside an exact named group."""
        _name(name)
        group = self._group(parent_path, expected_parent_name)
        return self._owned_action(session_id, "add_group", {"name": name, **group})

    def _group(self, path, name):
        if path is None:
            if name is not None:
                raise ValueError("Group name requires a group path")
            return {"group_path": None, "expected_group_name": None}
        validate_edits([{"path": path, "expected_name": name, "visible": True}])
        return {"group_path": path, "expected_group_name": name}

    def place_image(self, session_id: str, source: str, name: str, position: list,
                    *, group_path=None, expected_group_name=None):
        """Embed a PNG on a new layer; no linked source is retained."""
        _name(name)
        if not isinstance(position, list) or len(position) != 2:
            raise ValueError("position must be x,y")
        for value in position:
            _number(value, "position", 0, 30000)
        group = self._group(group_path, expected_group_name)
        image = self._path(source, suffix={".png"}, exists=True)
        # Parse before dispatch, but never rewrite the caller's source image.
        with tempfile.TemporaryDirectory(prefix="cavi-adobe-placement-") as directory:
            staged = Path(directory) / "placed.png"
            source_hash = digest(image)
            shutil.copyfile(image, staged)
            _png(staged, inspect_only=True)
            if digest(image) != source_hash:
                raise ValueError("Placement source changed during staging")
            result = self._owned_action(session_id, "place_image", {
                "source": str(staged), "name": name, "position": position, **group})
            if digest(image) != source_hash:
                raise AdobeOutcomeUnknown("Placement source changed during the command")
            return {**result, "placement_source": self._artifact(image)}

    def export(self, session_id: str, destination: str, *, kind: Literal["png", "svg"] | None = None,
               usage: Literal["color", "data"] = "color"):
        if kind is None:
            kind = "svg" if self.app == "illustrator" else "png"
        if kind not in ("png", "svg") or usage not in ("color", "data"):
            raise ValueError("Choose png/svg and color/data explicitly")
        if kind == "svg" and (self.app != "illustrator" or usage != "color"):
            raise ValueError("SVG export requires Illustrator and color usage")
        target = self._path(destination, suffix={"." + kind})
        if not target.parent.is_dir():
            raise ValueError("Export destination directory must already exist")
        receipt_path = self._path(str(target) + ".receipt.json")
        # Keep staging until successful publication or explicit inspection of an unknown outcome.
        directory = Path(tempfile.mkdtemp(prefix=".cavi-adobe-export-", dir=target.parent))
        staged = directory / ("export." + kind)

        def publish(result):
            native_hash = digest(staged)
            interpretation = (_png(staged, data=usage == "data",
                                   color_profile=result["document"].get("export_profile"))
                              if kind == "png" else _svg(staged, native_export=True))
            os.link(staged, target)
            receipt = {**result, "artifact": {**self._artifact(target), **interpretation},
                       "native_export_sha256": native_hash, "visual_acceptance": "unreviewed"}
            with receipt_path.open("x") as stream:
                json.dump(receipt, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            shutil.rmtree(directory)
            return {**receipt, "receipt": self._artifact(receipt_path)}

        return self._owned_action(session_id, "export", {
            "destination": str(staged), "kind": kind, "usage": usage}, publish)

    def fonts(self):
        """Read installed native font names without changing a document."""
        return self._read("fonts")

    def add_path(self, session_id, name, points, *, closed=True, rgb=None,
                 group_path=None, expected_group_name=None):
        _name(name)
        group = self._group(group_path, expected_group_name)
        if type(closed) is not bool or not isinstance(points, list) or not 2 <= len(points) <= 1024:
            raise ValueError("Path requires 2..1024 Bézier points and an explicit closed boolean")
        for point in points:
            if not isinstance(point, dict) or set(point) != {"anchor", "left", "right"}:
                raise ValueError("Each point requires anchor, left and right coordinate pairs")
            for pair in point.values():
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("Path coordinates must be pairs")
                for value in pair:
                    _number(value, "path coordinate", -30000, 30000)
        if rgb is not None:
            self._rgb(rgb)
            if not closed:
                raise ValueError("Filled paths must be closed")
        return self._owned_action(session_id, "add_path", {
            "name": name, "points": points, "closed": closed, "rgb": rgb, **group})

    @staticmethod
    def _rgb(rgb):
        if not isinstance(rgb, list) or len(rgb) != 3:
            raise ValueError("rgb requires three floating channel values in 0..255")
        for value in rgb:
            _number(value, "rgb", 0, 255)

    def add_text(self, session_id, name, text, font, position, size, rgb):
        _name(name)
        _name(font)
        if not isinstance(text, str) or not 1 <= len(text) <= 10000 or "\x00" in text:
            raise ValueError("Text requires 1..10000 characters without NUL")
        if not isinstance(position, list) or len(position) != 2:
            raise ValueError("Text position requires x,y")
        for value in position:
            _number(value, "position", -30000, 30000)
        _number(size, "font size", .1, 1296)
        self._rgb(rgb)
        return self._owned_action(session_id, "add_text", {
            "name": name, "text": text, "font": font, "position": position, "size": size, "rgb": rgb})

    def apply_mask(self, session_id, layer_path, expected_name, path_name):
        validate_edits([{"path": layer_path, "expected_name": expected_name, "visible": True}])
        _name(path_name)
        return self._owned_action(session_id, "apply_mask", {
            "layer_path": layer_path, "expected_name": expected_name, "path_name": path_name})

    def close_copy(self, session_id: str):
        """Close only this session's retained native document reference, without saving."""
        return self._owned_action(session_id, "close_copy", {})
