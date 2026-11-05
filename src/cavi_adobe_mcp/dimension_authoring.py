"""Bounded Adobe Dimension status, render submission and output inspection."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from cavi_mcp_core import apps
from cavi_mcp_core.contracts import OperationRejected, OperationUnknown
from cavi_mcp_core.journal import write_json
from PIL import Image

from . import native

BUNDLE_ID = "com.adobe.dimension"
APP_PATH = Path("/Applications/Adobe Dimension/Adobe Dimension.app")
SOURCE = Path(__file__).with_name("dimension_native.swift")
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
PNG_MAX_BYTES = 512 * 1024 * 1024
PNG_MAX_PIXELS = 120_000_000


class DimensionRejected(OperationRejected):
    """The requested launch or render submission was rejected before that action was dispatched."""

    def __init__(self, message, code="INVALID_ARGUMENT"):
        super().__init__(message, code)


class DimensionUnknown(OperationUnknown):
    """The native render may have been submitted; inspect the same job before retrying."""


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()




def write_json_exclusive(path: Path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def application():
    """The Dimension bundle at APP_PATH: the native helper launches only that bundle."""
    try:
        return apps.bundle("Adobe Dimension", BUNDLE_ID, APP_PATH.name, None, APP_PATH.parent,
                           path=APP_PATH).info
    except (apps.AppUnavailable, apps.AppMismatch) as exc:
        raise DimensionRejected(str(exc), exc.error_code) from exc


class DimensionBridge:
    """Compile lazily and call only the fixed local native adapter."""

    def __init__(self, directory, timeout=30):
        self.directory = Path(directory)
        self.timeout = timeout

    def _executable(self):
        application()
        try:
            return native.build_helper(SOURCE, self.directory, "dimension-native")
        except native.HelperError as exc:
            raise DimensionRejected(str(exc), exc.code) from exc

    def _call(self, request, expected_operation):
        binary = self._executable()
        try:
            result = subprocess.run([str(binary)], input=json.dumps(request),
                                    capture_output=True, text=True,
                                    timeout=max(self.timeout, 60) if expected_operation == "render_start" else self.timeout,
                                    check=False)
        except subprocess.TimeoutExpired as exc:
            raise DimensionUnknown("Dimension request timed out; inspect the existing render job") from exc
        if result.returncode:
            raise DimensionUnknown("Dimension adapter did not return a native receipt: " + result.stderr[-1000:])
        try:
            packet = json.loads(result.stdout)
        except (ValueError, UnicodeError) as exc:
            raise DimensionUnknown("Dimension adapter returned invalid JSON") from exc
        if (not isinstance(packet, dict) or packet.get("version") != 1
                or packet.get("operation") != expected_operation or type(packet.get("ok")) is not bool):
            raise DimensionUnknown("Dimension adapter response identity is invalid")
        if not packet["ok"]:
            error = packet.get("error")
            if isinstance(error, dict):
                code = error.get("code", "OUTCOME_UNKNOWN")
                message = str(error.get("message", "Native render failed"))[:1000]
                phase = error.get("phase")
                dispatched = error.get("render_dispatched")
                details = []
                if isinstance(phase, str) and phase:
                    details.append(f"phase={phase[:128]}")
                if type(dispatched) is bool:
                    details.append(f"render_dispatched={str(dispatched).lower()}")
                if details:
                    message = f"{message} ({', '.join(details)})"
                if error.get("retry_safe") is not False:
                    raise DimensionUnknown("Native error did not explicitly prohibit retry; inspect the job")
                if error.get("outcome") == "rejected":
                    raise DimensionRejected(message, code)
                if error.get("outcome") == "unknown":
                    raise DimensionUnknown(message, code)
                raise DimensionUnknown("Native render returned an unrecognized outcome; inspect the job", code)
            # Preserve v1 observation error behavior while treating render errors as unknown.
            if expected_operation in {"status", "inspect"}:
                raise DimensionRejected(str(error or "Native observation failed"))
            raise DimensionUnknown(str(error or "Native render outcome is unknown"))
        value = packet.get("result")
        if not isinstance(value, dict):
            raise DimensionUnknown("Dimension adapter result is not an object")
        return value

    def call(self, operation):
        if operation == "launch":
            value = self._call({"version": 1, "operation": "launch"}, "launch")
            if (value.get("status") not in {"launched", "already_running"}
                    or value.get("bundle_id") != BUNDLE_ID
                    or value.get("bundle_path") != str(APP_PATH)
                    or value.get("launches_application") is not (value.get("status") == "launched")):
                raise DimensionUnknown("Dimension launch result did not match the fixed installed application")
            identity = value.get("process_identity")
            if (not isinstance(identity, dict) or not isinstance(identity.get("facts"), dict)
                    or not re.fullmatch(r"[0-9a-f]{64}", str(identity.get("sha256", "")))):
                raise DimensionUnknown("Dimension launch result has no verified process identity")
            return value
        if operation not in {"status", "inspect"}:
            raise DimensionRejected("Use the typed Dimension workflow for mutations")
        # Keep the original status/inspect request envelope unchanged.
        return self._call({"operation": operation}, operation)

    def render_start(self, parameters, expected_process_identity):
        return self._call({"version": 1, "operation": "render_start", "parameters": parameters,
                           "expected_process_identity": expected_process_identity}, "render_start")


class DimensionAuthoring:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("Dimension workspace must be a directory")
        self.store = self.workspace / ".dimension-authoring"
        self.bridge = DimensionBridge(self.store)

    def status(self):
        return self.bridge.call("status")

    def launch(self):
        """Open only the fixed installed Dimension bundle, reusing an existing process."""
        return self.bridge.call("launch")

    def inspect(self):
        return self.bridge.call("inspect")

    def _path(self, value, *, suffix=None, exists=False):
        if (not isinstance(value, str) or not value or Path(value).is_absolute()
                or any(ord(c) < 32 for c in value) or ".." in Path(value).parts):
            raise DimensionRejected("Use a workspace-relative path without traversal or control characters")
        logical = self.workspace / value
        path = logical.resolve()
        if (not path.is_relative_to(self.workspace) or path == self.workspace
                or logical != path):
            raise DimensionRejected("Paths must stay inside the workspace without symlinks")
        if suffix and path.suffix.lower() not in suffix:
            raise DimensionRejected("Unsupported file extension")
        if exists and (not path.is_file() or path.is_symlink()):
            raise DimensionRejected("Input must be a regular workspace file")
        if exists is False and path.exists():
            raise DimensionRejected("Output directory already exists")
        return path

    def _store(self):
        self.store.mkdir(mode=0o700, exist_ok=True)
        if self.store.is_symlink() or self.store.resolve() != self.store:
            raise DimensionRejected("Dimension job store must not be a symlink", "STATE_UNSAFE")
        jobs = self.store / "jobs"
        jobs.mkdir(mode=0o700, exist_ok=True)
        if jobs.is_symlink() or jobs.resolve() != jobs:
            raise DimensionRejected("Dimension job records must not be symlinks", "STATE_UNSAFE")
        return jobs

    @contextmanager
    def _locked(self):
        self._store()
        lock_path = self.store / "lock"
        if lock_path.is_symlink():
            raise DimensionRejected("Dimension lock must not be a symlink", "STATE_UNSAFE")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DimensionRejected("Another Dimension render request is active", "JOB_BUSY") from exc
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _active_path(self):
        return self.store / "active.json"

    @staticmethod
    def _inspect_hint(operation_id):
        return (f"operation_id={operation_id}; call dimension_render_inspect with "
                f"operation_id='{operation_id}' before starting another render")

    def _job_path(self, operation_id):
        if not isinstance(operation_id, str) or not JOB_ID.fullmatch(operation_id):
            raise DimensionRejected("Invalid Dimension render operation id")
        return self.store / "jobs" / (operation_id + ".json")

    def _active_matches(self, operation_id):
        active_path = self._active_path()
        if active_path.is_symlink():
            raise DimensionUnknown("Dimension active job journal is unsafe", "STATE_UNSAFE")
        if not active_path.is_file():
            return None
        active = json.loads(active_path.read_text())
        if active.get("operation_id") != operation_id:
            active_id = active.get("operation_id")
            if isinstance(active_id, str) and JOB_ID.fullmatch(active_id):
                raise DimensionUnknown(
                    f"A different Dimension render remains active ({self._inspect_hint(active_id)})", "JOB_ACTIVE")
            raise DimensionUnknown("A different Dimension render remains active", "JOB_ACTIVE")
        return active

    def _mark_unknown(self, job_path, record, message):
        record.update(state="unknown", last_error=str(message)[:1000], updated_at=time.time())
        write_json(job_path, record)
        active_path = self._active_path()
        if active_path.is_file() and not active_path.is_symlink():
            active = json.loads(active_path.read_text())
            if active.get("operation_id") == record.get("operation_id"):
                active.update(state="unknown", updated_at=time.time())
                write_json(active_path, active)

    def _write_receipt(self, output, receipt, job_path, record):
        try:
            write_json_exclusive(output.parent / "receipt.json", receipt)
        except FileExistsError as exc:
            self._mark_unknown(job_path, record, "A receipt appeared before this render completed")
            raise DimensionUnknown("A render receipt already exists; it was preserved") from exc

    def _copy_source(self, source, target, source_sha256):
        identity = None
        try:
            with source.open("rb") as reader, target.open("xb") as writer:
                created = os.fstat(writer.fileno())
                identity = (created.st_dev, created.st_ino)
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
            if digest(source) != source_sha256 or digest(target) != source_sha256:
                raise DimensionRejected("Scene changed while copying its owned render input")
            return source_sha256
        except BaseException:
            if identity is not None:
                try:
                    current = target.lstat()
                    if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == identity:
                        target.unlink()
                except FileNotFoundError:
                    pass
            raise

    def _prepare_render_locked(self, source, target):
        active_path = self._active_path()
        if active_path.exists() or active_path.is_symlink():
            active_id = None
            if active_path.is_file() and not active_path.is_symlink():
                try:
                    active_id = json.loads(active_path.read_text()).get("operation_id")
                except (OSError, ValueError, AttributeError):
                    pass
            if isinstance(active_id, str) and JOB_ID.fullmatch(active_id):
                raise DimensionUnknown(
                    f"Dimension render is still active ({self._inspect_hint(active_id)})", "JOB_ACTIVE")
            raise DimensionUnknown("An unresolved Dimension render remains active; inspect its journal first", "JOB_ACTIVE")
        state = self.status()
        if not state.get("running") or not isinstance(state.get("process_identity"), dict):
            raise DimensionRejected("Call dimension_launch before rendering", "APP_NOT_RUNNING")
        if state.get("authentication_or_permission_prompt"):
            raise DimensionRejected("Resolve the visible Dimension prompt manually", "PROMPT_BLOCKED")
        process_identity = state["process_identity"]
        if not re.fullmatch(r"[0-9a-f]{64}", str(process_identity.get("sha256", ""))):
            raise DimensionRejected("Dimension process identity is invalid", "APP_IDENTITY_MISMATCH")
        source_hash = digest(source)
        operation_id = uuid.uuid4().hex
        try:
            target.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise DimensionRejected("Render output directory already exists") from exc
        owned_copy = target / "render.dn"
        try:
            copy_hash = self._copy_source(source, owned_copy, source_hash)
        except BaseException:
            if not any(target.iterdir()):
                target.rmdir()
            raise
        output = target / "render.png"
        record = {"version": 1, "operation_id": operation_id, "workspace": str(self.workspace),
                  "state": "prepared", "created_at": time.time(), "updated_at": time.time(),
                  "source": str(source.relative_to(self.workspace)), "source_sha256": source_hash,
                  "owned_copy": str(owned_copy.relative_to(self.workspace)),
                  "owned_copy_sha256": copy_hash, "directory": str(target.relative_to(self.workspace)),
                  "output": str(output.relative_to(self.workspace)), "process_identity": process_identity,
                  "captures": []}
        job_path = self._store() / (operation_id + ".json")
        write_json(job_path, record)
        write_json(active_path, {"operation_id": operation_id, "workspace": str(self.workspace),
                                 "state": "prepared", "created_at": record["created_at"]})
        return record, job_path, active_path, source, owned_copy, target, output

    def render_start(self, scene, directory):
        source = self._path(scene, suffix={".dn"}, exists=True)
        target = self._path(directory)
        if not target.parent.is_dir():
            raise DimensionRejected("Render output parent directory must already exist")
        with self._locked():
            record, job_path, active_path, source, owned_copy, target, output = \
                self._prepare_render_locked(source, target)
            operation_id = record["operation_id"]
            source_hash = record["source_sha256"]
            copy_hash = record["owned_copy_sha256"]
            process_identity = record["process_identity"]
            parameters = {"workspace": str(self.workspace), "source": str(owned_copy),
                          "directory": str(target), "basename": "render"}
            try:
                native = self.bridge.render_start(parameters, process_identity)
            except DimensionRejected as exc:
                record.update(state="rejected", last_error=str(exc), updated_at=time.time())
                write_json(job_path, record)
                active_path.unlink()
                raise DimensionRejected(f"{exc}; {self._inspect_hint(operation_id)}", exc.error_code) from exc
            except DimensionUnknown as exc:
                self._mark_unknown(job_path, record, exc)
                raise DimensionUnknown(f"{exc}; {self._inspect_hint(operation_id)}", exc.error_code) from exc
            except BaseException as exc:
                self._mark_unknown(job_path, record, exc)
                if not isinstance(exc, Exception):
                    raise
                raise DimensionUnknown(
                    f"Dimension render outcome is unknown ({self._inspect_hint(operation_id)})") from exc
            try:
                if self.status().get("process_identity") != process_identity:
                    raise DimensionUnknown("Dimension process identity changed after render submission",
                                            "APP_IDENTITY_MISMATCH")
                if native.get("status") != "render_requested":
                    raise DimensionUnknown("Dimension did not confirm render submission")
                if native.get("output") != str(output):
                    raise DimensionUnknown("Dimension render output path does not match the owned job directory")
                captures = native.get("captures", [])
                if (not isinstance(captures, list) or len(captures) > 8
                        or any(not isinstance(item, str) for item in captures)):
                    raise DimensionUnknown("Dimension render captures are invalid")
                if native.get("source_identity") != "opened_owned_copy":
                    raise DimensionUnknown("Dimension did not confirm opening the owned scene copy")
                if digest(source) != source_hash or digest(owned_copy) != copy_hash:
                    raise DimensionUnknown("Scene or owned copy changed during render submission")
                record.update(state="render_requested", updated_at=time.time(), native_status=native["status"],
                              output_identity=native["source_identity"], captures=captures)
                active = {"operation_id": operation_id, "workspace": str(self.workspace),
                          "state": "render_requested", "created_at": record["created_at"]}
                write_json(job_path, record)
                write_json(active_path, active)
            except BaseException as exc:
                self._mark_unknown(job_path, record, exc)
                if not isinstance(exc, Exception):
                    raise
                code = getattr(exc, "error_code", "OUTCOME_UNKNOWN")
                raise DimensionUnknown(f"{exc}; {self._inspect_hint(operation_id)}", code) from exc
            return {"operation_id": operation_id, "status": "render_requested", "output": str(output),
                    "directory": str(target), "owned_copy": str(owned_copy), "captures": captures,
                    "source_identity": native["source_identity"], "input_sha256": source_hash,
                    "owned_copy_sha256": copy_hash, "process_identity": process_identity,
                    "retry_safe": False}

    def render_inspect(self, operation_id):
        job_path = self._job_path(operation_id)
        with self._locked():
            if job_path.is_symlink() or not job_path.is_file():
                raise DimensionRejected("Dimension render operation is missing or unsafe")
            record = json.loads(job_path.read_text())
            if (record.get("operation_id") != operation_id or record.get("workspace") != str(self.workspace)):
                raise DimensionRejected("Dimension render operation identity does not match this workspace")
            if record.get("state") in {"complete", "unsupported"}:
                return record["receipt"]
            active_path = self._active_path()
            active = self._active_matches(operation_id)
            if record.get("state") == "rejected":
                return {"operation_id": operation_id, "status": "rejected", "error": record.get("last_error")}
            source = self._path(record.get("source", ""), suffix={".dn"}, exists=True)
            owned_copy = self._path(record.get("owned_copy", ""), suffix={".dn"}, exists=True)
            if digest(source) != record.get("source_sha256") or digest(owned_copy) != record.get("owned_copy_sha256"):
                self._mark_unknown(job_path, record, "Scene or owned copy changed after submission")
                raise DimensionUnknown("Scene or owned copy changed; render remains unresolved")
            output = self._path(record.get("output", ""), suffix={".png"}, exists=None)
            if not output.exists():
                return {"operation_id": operation_id, "status": "pending", "output": str(output),
                        "native_status": record.get("native_status", "unknown"), "retry_safe": False}
            if output.is_symlink() or not output.is_file():
                self._mark_unknown(job_path, record, "Render output is not a regular file")
                raise DimensionUnknown("Dimension render output is unsafe; job remains unresolved")
            size = output.stat().st_size
            if not 1 <= size <= PNG_MAX_BYTES:
                self._mark_unknown(job_path, record, "Render PNG size is outside the bounded range")
                raise DimensionUnknown("Dimension render output size is outside the bounded range")
            first_hash = digest(output)
            try:
                with output.open("rb") as stream:
                    header = stream.read(29)
                if len(header) != 29 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
                    raise ValueError("Output is not a valid PNG")
                bit_depth = header[24]
                with Image.open(output) as image:
                    if image.format != "PNG" or not image.width or not image.height:
                        raise ValueError("Output is not a valid PNG")
                    if image.width * image.height > PNG_MAX_PIXELS:
                        raise ValueError("PNG exceeds the pixel bound")
                    width, height, mode = image.width, image.height, image.mode
                    icc_profile = image.info.get("icc_profile")
                    image.load()
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                self._mark_unknown(job_path, record, exc)
                raise DimensionUnknown("Dimension output PNG did not load safely") from exc
            if digest(output) != first_hash or digest(source) != record["source_sha256"] \
                    or digest(owned_copy) != record["owned_copy_sha256"]:
                self._mark_unknown(job_path, record, "A render input or output changed during verification")
                raise DimensionUnknown("Dimension render artifacts changed during verification")
            if bit_depth != 8 or mode not in {"RGB", "RGBA"}:
                receipt = {"operation_id": operation_id, "status": "unsupported", "output": str(output),
                           "sha256": first_hash, "bytes": size, "width": width, "height": height,
                           "mode": mode, "bit_depth": bit_depth,
                           "reason": "Only 8-bit RGB and RGBA PNG renders can be handed to the game pipeline",
                           "input": str(source), "input_sha256": record["source_sha256"],
                           "owned_copy": str(owned_copy), "owned_copy_sha256": record["owned_copy_sha256"],
                           "source_identity": record.get("output_identity"),
                           "captures": record.get("captures", []),
                           "verified_at": time.time()}
                self._write_receipt(output, receipt, job_path, record)
                record.update(state="unsupported", updated_at=time.time(), receipt=receipt)
                write_json(job_path, record)
                if active is not None:
                    current = json.loads(active_path.read_text())
                    if current.get("operation_id") == operation_id:
                        active_path.unlink()
                return receipt

            handoff = output
            handoff_hash = first_hash
            converted = False
            if mode == "RGB":
                handoff = output.with_name(output.stem + "-rgba.png")
                try:
                    descriptor = os.open(handoff, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "wb") as stream, Image.open(output) as source_image:
                        source_image.load()
                        rgba_image = source_image.convert("RGBA")
                        rgba_image.putalpha(255)
                        rgba_image.save(stream, format="PNG", icc_profile=icc_profile)
                    with Image.open(handoff) as rgba_check, Image.open(output) as rgb_check:
                        rgba_check.load()
                        rgb_check.load()
                        if (rgba_check.mode != "RGBA" or rgba_check.size != rgb_check.size
                                or rgba_check.getchannel("A").getextrema() != (255, 255)
                                or rgba_check.convert("RGB").tobytes() != rgb_check.tobytes()
                                or rgba_check.info.get("icc_profile") != icc_profile):
                            raise ValueError("RGBA derivative did not preserve source RGB pixels and ICC profile")
                    converted = True
                    handoff_hash = digest(handoff)
                except BaseException as exc:
                    # Keep any partial derivative as evidence; never overwrite or silently retry it.
                    self._mark_unknown(job_path, record, "RGBA handoff conversion failed: " + str(exc))
                    raise DimensionUnknown("Dimension render was retained, but RGBA handoff conversion failed") from exc
            if digest(output) != first_hash or digest(source) != record["source_sha256"] \
                    or digest(owned_copy) != record["owned_copy_sha256"]:
                self._mark_unknown(job_path, record, "A source or render changed during handoff conversion")
                raise DimensionUnknown("Dimension render artifacts changed during handoff conversion")
            receipt = {"operation_id": operation_id, "status": "complete", "output": str(output),
                       "sha256": first_hash, "bytes": size, "width": width, "height": height, "mode": mode,
                       "bit_depth": bit_depth, "handoff_output": str(handoff),
                       "handoff_sha256": handoff_hash, "handoff_converted": converted,
                       "input": str(source), "input_sha256": record["source_sha256"],
                       "owned_copy": str(owned_copy), "owned_copy_sha256": record["owned_copy_sha256"],
                       "source_identity": record.get("output_identity"), "captures": record.get("captures", []),
                       "process_identity": record["process_identity"], "verified_at": time.time()}
            self._write_receipt(output, receipt, job_path, record)
            record.update(state="complete", updated_at=time.time(), receipt=receipt)
            write_json(job_path, record)
            if active is not None:
                current = json.loads(active_path.read_text())
                if current.get("operation_id") == operation_id:
                    active_path.unlink()
            return receipt
