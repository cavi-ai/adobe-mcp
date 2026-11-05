"""Bounded Adobe Animate authoring through a fixed, data-only JSFL driver."""

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
import subprocess
import sys
import tempfile
import time
import uuid
from fractions import Fraction
from pathlib import Path
from threading import Event

from cavi_mcp_core import apps
from cavi_mcp_core.contracts import OperationRejected, OperationUnknown
from cavi_mcp_core.journal import archive, completion, fingerprint, write_json
from PIL import Image

from . import art_media, native

APP_ID = "com.adobe.Adobe-Animate-2024.application"
APP_NAME = "Adobe Animate 2024"
APP_GLOB = "Adobe Animate*/*.app"
APP_ENV = "CAVI_ANIMATE_APP"
DRIVER = Path(__file__).with_name("animate_authoring.jsfl")
NATIVE_HELPER = Path(__file__).with_name("animate_native.swift")
SESSION_ID = re.compile(r"^[0-9a-f]{32}$")


class AnimateRejected(OperationRejected):
    """A native preflight rejected the request before mutation."""


class AnimateUnknown(OperationUnknown):
    """The native operation may have run; inspect app and outputs before continuing."""


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def snapshot_receipt(workspace: Path, path: Path, result: dict):
    if (result.get("snapshot_uri") != path.as_uri() or not path.is_file() or path.is_symlink()):
        raise AnimateUnknown("Animate did not retain the requested native FLA snapshot")
    size = path.stat().st_size
    if not 1 <= size <= 250 * 1024 * 1024:
        raise AnimateUnknown("Native FLA snapshot is empty or exceeds 250 MiB")
    return {"path": str(path.relative_to(workspace)), "sha256": digest(path), "size_bytes": size}




def launcher_source(request_uri: str, response_uri: str, cancel_uri: str) -> str:
    """Generate only fixed JSFL that calls the bundled entry point with file URIs."""
    return ("fl.runScript(" + json.dumps(DRIVER.as_uri()) + ", \"caviDispatch\", "
            + json.dumps(request_uri) + ", " + json.dumps(response_uri) + ", "
            + json.dumps(cancel_uri) + ");\n")


def validate_timeline_edits(edits):
    from .timeline_models import TimelineBatch
    return TimelineBatch(edits=edits).model_dump(exclude_none=True)["edits"]


class AnimateAuthoring:
    def __init__(self, workspace, *, app_path=None, timeout=60, dispatcher=None):
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("Workspace must be a directory")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 5 <= timeout <= 180:
            raise ValueError("Timeout must be in 5..180 seconds")
        self.timeout = timeout
        self.app_path = Path(app_path).resolve() if app_path else None
        self.store = self.workspace / ".animate-authoring"
        self.dispatcher = dispatcher
        self._volume_name = None

    def _path(self, value, *, suffix=None, exists=False):
        if (not isinstance(value, str) or not value or any(ord(c) < 32 for c in value)
                or ".." in Path(value).parts):
            raise AnimateRejected("Use a workspace path without traversal or control characters")
        logical = self.workspace / value
        path = logical.resolve()
        if not path.is_relative_to(self.workspace) or path == self.workspace or logical != path:
            raise AnimateRejected("Paths must stay inside the shared workspace without symlinks")
        if suffix and path.suffix.lower() not in suffix:
            raise AnimateRejected("Unsupported file extension")
        if exists:
            if not path.is_file() or path.is_symlink():
                raise AnimateRejected("Input must be a regular workspace file")
        elif path.exists():
            raise AnimateRejected("Output must not already exist")
        return path

    def _app(self):
        """The Animate bundle: ``app_path``, else ``CAVI_ANIMATE_APP``, else /Applications."""
        try:
            return apps.bundle(APP_NAME, APP_ID, APP_GLOB, APP_ENV, path=self.app_path)
        except (apps.AppUnavailable, apps.AppMismatch) as exc:
            raise AnimateRejected(str(exc), exc.error_code) from exc

    def status(self):
        """Read app installation and process state only; never launch Animate."""
        app = self._app()
        version = app.info.get("CFBundleShortVersionString", "unknown")
        if sys.platform != "darwin":
            return {"application": str(app.path), "version": version,
                    "running": False, "launches_application": False}
        matches = [process._asdict() for process in apps.processes(app.executable)]
        if len(matches) > 1:
            raise AnimateRejected("Multiple Animate processes found; select one instance", "APP_AMBIGUOUS")
        return {"application": str(app.path), "version": version,
                "running": bool(matches), "process": matches[0] if matches else None,
                "launches_application": False, "authenticates": False}

    def _lock(self):
        self.store.mkdir(mode=0o700, exist_ok=True)
        if self.store.is_symlink():
            raise AnimateRejected("Animate state directory must not be a symlink", "STATE_UNSAFE")
        stream = (self.store / "lock").open("a+")
        fcntl.flock(stream, fcntl.LOCK_EX)
        return stream

    def _root_volume_name(self):
        if self._volume_name is not None:
            return self._volume_name
        try:
            result = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", "/"],
                                    capture_output=True, timeout=5, check=False)
            info = plistlib.loads(result.stdout) if result.returncode == 0 else {}
        except (OSError, plistlib.InvalidFileException, subprocess.TimeoutExpired) as exc:
            raise AnimateRejected("Could not verify the macOS root volume name", "VOLUME_IDENTITY_UNAVAILABLE") from exc
        volume = info.get("VolumeName")
        if (not isinstance(volume, str) or not volume or "/" in volume
                or any(ord(character) < 32 for character in volume)):
            raise AnimateRejected("macOS root volume name is invalid", "VOLUME_IDENTITY_UNAVAILABLE")
        self._volume_name = volume
        return volume

    def _native_helper(self):
        try:
            return native.build_helper(NATIVE_HELPER, self.store, "animate-native")
        except native.HelperError as exc:
            raise AnimateRejected(str(exc), exc.code) from exc

    def _handle_jsfl_prompt(self, process, operation_id, launcher):
        """Run only the fixed handler for the exact per-operation Animate JSFL chooser."""
        request = {"operation": "handle_jsfl_prompt", "operation_id": operation_id,
                   "launcher": launcher, "application": str(self._app().path),
                   "pid": process["pid"], "started": process["started"], "wait_seconds": 3.0}
        try:
            result = subprocess.run([str(self._native_helper())], input=json.dumps(request),
                                    capture_output=True, text=True, timeout=5, check=False)
        except subprocess.TimeoutExpired as exc:
            raise AnimateUnknown("Animate JSFL chooser handler timed out; inspect the app before continuing") from exc
        if result.returncode:
            raise AnimateUnknown("Animate JSFL chooser handler failed: " + result.stderr.strip()[-1000:])
        try:
            packet = json.loads(result.stdout)
        except (ValueError, UnicodeError) as exc:
            raise AnimateUnknown("Animate JSFL chooser handler returned invalid JSON") from exc
        if (not isinstance(packet, dict) or packet.get("version") != 1
                or packet.get("operation") != "handle_jsfl_prompt"
                or type(packet.get("ok")) is not bool):
            raise AnimateUnknown("Animate JSFL chooser handler response is invalid")
        if not packet["ok"]:
            error = packet.get("error", {})
            raise AnimateUnknown("Animate JSFL chooser was not handled: " + str(error.get("message", error)))
        response = packet.get("result")
        if (not isinstance(response, dict) or response.get("operation_id") != operation_id
                or response.get("status") not in {"absent", "submitted"}
                or (response.get("status") == "submitted" and response.get("launcher") != launcher)):
            raise AnimateUnknown("Animate JSFL chooser response does not match the operation")
        return response

    def _native(self, request, cancelled: Event | None = None, on_dispatch=None):
        state = self.status()
        if not state["running"]:
            raise AnimateRejected("Open Animate manually before authoring", "APP_NOT_RUNNING")
        if (request.get("expected_process") is not None
                and request["expected_process"] != state.get("process")):
            raise AnimateRejected("Animate process changed before dispatch", "APP_IDENTITY_MISMATCH")
        if self.dispatcher:
            if cancelled is not None and cancelled.is_set():
                raise AnimateRejected("Request cancelled before native dispatch", "CANCELLED")
            if on_dispatch:
                on_dispatch()
            response = self.dispatcher(request, state)
            if self.status().get("process") != state.get("process"):
                raise AnimateUnknown("Animate process identity changed during the command")
            if (not isinstance(response, dict) or response.get("operation_id") != request["operation_id"]
                    or response.get("version") != 1 or type(response.get("ok")) is not bool
                    or not isinstance(response.get("result"), dict)):
                raise AnimateUnknown("Animate receipt identity does not match the request")
            if not response["ok"]:
                raise AnimateUnknown("Animate command failed: " + str(response.get("error", "unknown error")))
            return response
        if sys.platform != "darwin":
            raise AnimateRejected("Native Animate authoring requires macOS", "PLATFORM_UNSUPPORTED")
        directory = self.store / ("dispatch-" + request["operation_id"])
        directory.mkdir(mode=0o700)
        request_path, response_path = directory / "request.json", directory / "response.json"
        launcher_path, cancel_path = directory / ("animate-" + request["operation_id"] + ".jsfl"), directory / "cancel"
        request_uri, response_uri, cancel_uri = (request_path.as_uri(), response_path.as_uri(), cancel_path.as_uri())
        native_request = {**request, "volume_name": self._root_volume_name()}
        write_json(request_path, native_request)
        launcher_path.write_text(launcher_source(request_uri, response_uri, cancel_uri))
        os.chmod(launcher_path, 0o600)
        deadline = time.monotonic() + self.timeout
        dispatched = False
        try:
            if cancelled is not None and cancelled.is_set():
                raise AnimateRejected("Request cancelled before opening its JSFL launcher", "CANCELLED")
            dispatched = True
            if on_dispatch:
                on_dispatch()
            try:
                result = subprocess.run(["/usr/bin/open", "-a", str(self._app().path), "--", str(launcher_path)],
                                        capture_output=True, text=True, timeout=min(self.timeout, 15), check=False)
            except subprocess.TimeoutExpired as exc:
                raise AnimateUnknown("LaunchServices open timed out; inspect Animate and its pending operation") from exc
            if result.returncode:
                raise AnimateUnknown("LaunchServices could not open the JSFL launcher: "
                                      + result.stderr.strip()[:1000])
            if self.status().get("process") != state.get("process"):
                raise AnimateUnknown("Animate process identity changed after opening the launcher")
            if not response_path.exists():
                self._handle_jsfl_prompt(state["process"], request["operation_id"], launcher_path.name)
            while True:
                if cancelled is not None and cancelled.is_set() and not cancel_path.exists():
                    cancel_path.touch(mode=0o600)
                if response_path.exists():
                    if response_path.is_symlink() or response_path.stat().st_size > 4 * 1024 * 1024:
                        raise AnimateUnknown("Animate response is unsafe or exceeds 4 MiB")
                    try:
                        response = json.loads(response_path.read_text())
                    except (ValueError, UnicodeError) as exc:
                        raise AnimateUnknown("Animate wrote an invalid response receipt") from exc
                    if (response.get("operation_id") != request["operation_id"]
                            or response.get("version") != 1 or type(response.get("ok")) is not bool):
                        raise AnimateUnknown("Animate receipt identity does not match the request")
                    if self.status().get("process") != state.get("process"):
                        raise AnimateUnknown("Animate process identity changed during the command")
                    if not response["ok"]:
                        raise AnimateUnknown("Animate command did not finish successfully: "
                                              + str(response.get("error", "unknown error"))[:1000])
                    if not request.get("retain_receipt"):
                        shutil.rmtree(directory)
                    return response
                if time.monotonic() >= deadline:
                    raise AnimateUnknown("Animate did not write a native receipt before timeout")
                time.sleep(.05)
        except BaseException:
            if dispatched and not cancel_path.exists():
                cancel_path.touch(mode=0o600)
            raise

    def inspect(self):
        lock = self._lock()
        try:
            pending_path = self.store / "pending.json"
            pending = json.loads(pending_path.read_text()) if pending_path.is_file() else None
            native = self._native({"version": 1, "operation": "inspect", "operation_id": uuid.uuid4().hex})
            return {"native": native["result"], "pending_operation": pending, "launches_application": False}
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def _session(self, session_id):
        if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
            raise AnimateRejected("Invalid Animate session id")
        path = self.store / (session_id + ".json")
        if path.is_symlink() or self.store.is_symlink() or not path.is_file():
            raise AnimateRejected("Animate session is missing or unsafe")
        packet = json.loads(path.read_text())
        if packet.get("session_id") != session_id or packet.get("workspace") != str(self.workspace):
            raise AnimateRejected("Animate session does not match this workspace")
        copy = self._path(packet.get("document", ""), suffix={".fla"}, exists=True)
        if digest(copy) != packet.get("document_sha256"):
            raise AnimateUnknown("Owned FLA changed outside this session")
        source = self._path(packet.get("source", ""), exists=True)
        if digest(source) != packet.get("source_sha256"):
            raise AnimateUnknown("Source asset changed after the Animate copy was created")
        if packet.get("state") == "closed":
            raise AnimateRejected("Owned FLA session is closed")
        return packet

    def _mutate(self, action, parameters, *, session=None, finalize=None, cancelled=None):
        lock = self._lock()
        pending = self.store / "pending.json"
        try:
            if pending.exists():
                raise AnimateUnknown("Resolve the earlier Animate operation before another mutation")
            state = self.status()
            if not state["running"]:
                raise AnimateRejected("Open Animate manually before authoring", "APP_NOT_RUNNING")
            if session is not None:
                if session.get("process") is not None and session.get("process") != state.get("process"):
                    raise AnimateRejected("Animate process changed since this document session was created",
                                          "APP_IDENTITY_MISMATCH")
                session["process"] = state.get("process")
            operation_id = uuid.uuid4().hex
            record = {"version": 1, "operation_id": operation_id, "action": action,
                      "state": "pending", "workspace": str(self.workspace),
                      "parameters": parameters,
                      "session_id": session["session_id"] if session else None,
                      "session": dict(session) if session else None, "dispatched": False,
                      "created_at": time.time(), "retry_safe": False}
            write_json(pending, record)
            dispatched = False

            def mark_dispatched():
                nonlocal dispatched
                dispatched = True
                record["dispatched"] = True
                write_json(pending, record)

            try:
                response = self._native({**parameters, "version": 1, "operation": action, "retain_receipt": True,
                                         "expected_process": state.get("process"),
                                         "operation_id": operation_id}, cancelled=cancelled,
                                      on_dispatch=mark_dispatched)
                if response.get("operation_id") != operation_id:
                    raise AnimateUnknown("Native response does not match the pending operation")
                result = response["result"]
                if finalize:
                    result = finalize(result)
                record.update(state="observed", result=result)
                receipt_dir = self.store / "operations"
                receipt_dir.mkdir(mode=0o700, exist_ok=True)
                write_json(receipt_dir / (operation_id + ".json"), record)
                pending.unlink()
                shutil.rmtree(self.store / ("dispatch-" + operation_id), ignore_errors=True)
                return {**result, "operation_id": operation_id, "retry_safe": False}
            except AnimateRejected:
                if dispatched:
                    record.update(state="unknown", failure={"code": "OUTCOME_UNKNOWN",
                                                              "message": "Native operation did not return a receipt"})
                    write_json(pending, record)
                    raise AnimateUnknown("Animate outcome is unknown; inspect the app and pending journal")
                pending.unlink(missing_ok=True)
                raise
            except BaseException as exc:
                record.update(state="unknown", failure={"code": getattr(exc, "error_code", "OUTCOME_UNKNOWN"),
                                                          "message": str(exc)[:2000]})
                write_json(pending, record)
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                raise AnimateUnknown("Animate outcome is unknown; inspect the app and pending journal") from exc
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def open_copy(self, source, destination, cancelled=None):
        source_path = self._path(source, suffix={".fla"}, exists=True)
        target = self._path(destination, suffix={".fla"})
        if not target.parent.is_dir():
            raise AnimateRejected("Copy destination directory must already exist")
        source_hash = digest(source_path)
        created_identity = None
        try:
            with source_path.open("rb") as source_stream, target.open("xb") as target_stream:
                created = os.fstat(target_stream.fileno())
                created_identity = (created.st_dev, created.st_ino)
                shutil.copyfileobj(source_stream, target_stream)
            if digest(source_path) != source_hash:
                raise AnimateRejected("Source changed while making the private copy")
        except BaseException:
            if created_identity is not None:
                try:
                    current = target.lstat()
                    if (stat.S_ISREG(current.st_mode)
                            and (current.st_dev, current.st_ino) == created_identity):
                        target.unlink()
                except FileNotFoundError:
                    pass
            raise
        session = {"session_id": uuid.uuid4().hex, "workspace": str(self.workspace),
                   "source": str(source_path.relative_to(self.workspace)), "source_sha256": source_hash,
                   "document": str(target.relative_to(self.workspace)), "document_sha256": digest(target),
                   "state": "owned"}

        def save(result):
            doc = result.get("document", {})
            if digest(source_path) != source_hash:
                raise AnimateUnknown("Source FLA changed during native copy opening")
            if doc.get("path_uri") != target.as_uri():
                raise AnimateUnknown("Animate did not open the exact owned FLA copy")
            self.store.mkdir(mode=0o700, exist_ok=True)
            write_json(self.store / (session["session_id"] + ".json"), session)
            return {"session": session["session_id"], "document": doc, "source_sha256": source_hash,
                    "document_sha256": digest(target)}

        return self._mutate("open_copy", {"document_uri": target.as_uri()}, session=session,
                            finalize=save, cancelled=cancelled)

    def create_from_png(self, source, destination, cancelled=None):
        image = self._path(source, suffix={".png"}, exists=True)
        with tempfile.TemporaryDirectory(prefix="animate-png-check-") as temp:
            staged = Path(temp) / "input.png"
            shutil.copyfile(image, staged)
            with Image.open(staged) as png:
                if png.format != "PNG" or png.mode != "RGBA" or getattr(png, "n_frames", 1) != 1:
                    raise AnimateRejected("Import requires a single-frame 8-bit RGBA PNG")
                png.load()
        target = self._path(destination, suffix={".fla"})
        if not target.parent.is_dir():
            raise AnimateRejected("Document destination directory must already exist")
        source_hash = digest(image)
        session = {"session_id": uuid.uuid4().hex, "workspace": str(self.workspace),
                   "source": str(image.relative_to(self.workspace)), "source_sha256": source_hash,
                   "document": str(target.relative_to(self.workspace)), "state": "pending"}
        stage_dir = self._path(str(self.store / "staging" / session["session_id"]))
        stage_dir.mkdir(mode=0o700, parents=True)
        stage_file = stage_dir / "document.fla"

        def save(result):
            doc = result.get("document", {})
            if digest(image) != source_hash:
                raise AnimateUnknown("Source PNG changed during native import")
            if doc.get("path_uri") != stage_file.as_uri() or not stage_file.is_file():
                raise AnimateUnknown("Animate did not save the private staged FLA")
            try:
                os.link(stage_file, target)
            except FileExistsError as exc:
                raise AnimateUnknown("Destination appeared during native import; staged FLA was retained") from exc
            opened = self._native({"version": 1, "operation": "publish_open",
                                   "operation_id": uuid.uuid4().hex,
                                   "expected_process": session["process"],
                                   "staging_uri": stage_file.as_uri(), "document_uri": target.as_uri()},
                                  cancelled=cancelled)
            final_doc = opened.get("result", opened).get("document", {})
            if final_doc.get("path_uri") != target.as_uri():
                raise AnimateUnknown("Animate did not open the exact published FLA")
            session.update(document_sha256=digest(target), state="owned")
            self.store.mkdir(mode=0o700, exist_ok=True)
            write_json(self.store / (session["session_id"] + ".json"), session)
            shutil.rmtree(stage_dir)
            return {"session": session["session_id"], "document": final_doc,
                    "source_sha256": source_hash, "document_sha256": session["document_sha256"]}

        try:
            return self._mutate("create_import", {"document_uri": target.as_uri(),
                                "staging_uri": stage_file.as_uri(), "source_uri": image.as_uri()},
                                session=session, finalize=save, cancelled=cancelled)
        except AnimateRejected:
            shutil.rmtree(stage_dir, ignore_errors=True)
            raise

    def export_frames(self, session_id, directory, cancelled=None):
        session = self._session(session_id)
        process = self.status()
        if not process["running"] or process.get("process") != session.get("process"):
            raise AnimateRejected("Animate process changed since this document session was created",
                                  "APP_IDENTITY_MISMATCH")
        target = self._path(directory)
        if not target.parent.is_dir():
            raise AnimateRejected("Export parent directory must already exist")
        native = self.inspect()["native"]
        documents = native.get("documents", [])
        exact = [doc for doc in documents if doc.get("path_uri") == self._path(session["document"], exists=True).as_uri()]
        if len(exact) != 1:
            raise AnimateRejected("The exact owned FLA must be open once in Animate", "DOCUMENT_IDENTITY_MISMATCH")
        doc = exact[0]
        count, rate = doc.get("frame_count"), doc.get("frame_rate")
        if type(count) is not int or not 1 <= count <= 1000 or type(rate) not in (int, float) or not 1 <= rate <= 120:
            raise AnimateRejected("Document timeline is outside the bounded export range")
        fps = Fraction(str(rate)).limit_denominator(10000)
        if abs(float(fps) - rate) > 1e-8 or count * fps.denominator / fps.numerator > 300:
            raise AnimateRejected("Document frame rate cannot be represented safely or exceeds 300 seconds")
        frame_paths = [target / f"frame_{index + 1:04d}.png" for index in range(count)]
        if target.exists() or any(path.exists() for path in frame_paths):
            raise AnimateRejected("Export directory or one of its frames already exists")
        target.mkdir(mode=0o700)
        requests = [path.as_uri() for path in frame_paths]
        snapshot_path = target / "document-source.fla"

        def receipt(result):
            if result.get("document", {}).get("path_uri") != self._path(session["document"], exists=True).as_uri():
                raise AnimateUnknown("Exported document identity changed")
            outputs = result.get("outputs", [])
            if (len(outputs) != count or any(not path.is_file() for path in frame_paths)
                    or any(item.get("index") != index or item.get("path_uri") != requests[index]
                           for index, item in enumerate(outputs))):
                raise AnimateUnknown("Animate did not produce the complete ordered frame sequence")
            source_snapshot = snapshot_receipt(self.workspace, snapshot_path, result)
            from PIL import Image
            frames = []
            for index, path in enumerate(frame_paths):
                with Image.open(path) as image:
                    if image.format != "PNG" or image.mode != "RGBA" or getattr(image, "n_frames", 1) != 1:
                        raise AnimateUnknown("Native export is not a single-frame RGBA PNG")
                    width, height = image.size
                    image.load()
                frames.append({"name": f"Frame{index + 1:04d}",
                               "path": str(path.relative_to(self.workspace)), "sha256": digest(path),
                               "width": width, "height": height, "rect": [0, 0, width, height]})
            receipt_path = target / "media.json"
            payload = {"schema": art_media.SCHEMA, "version": 1,
                       "name": Path(session["document"]).stem, "fps": {"numerator": fps.numerator,
                                                                          "denominator": fps.denominator},
                       "loop": False, "alpha": "straight", "coordinate_origin": "top_left",
                       "frames": frames,
                       "sources": [{"path": source_snapshot["path"], "sha256": source_snapshot["sha256"]},
                                   {"path": session["source"], "sha256": session["source_sha256"]}]}
            write_json(receipt_path, payload)
            validated = art_media.validate(self.workspace, str(receipt_path.relative_to(self.workspace)))
            return {"session": session_id, "document": result["document"],
                    "source_snapshot": source_snapshot,
                    "media": validated,
                    "frames": frames, "visual_acceptance": "unreviewed"}

        return self._mutate("export_frames", {"document_uri": self._path(session["document"], exists=True).as_uri(),
                                               "frame_uris": requests, "snapshot_uri": snapshot_path.as_uri()},
                            session=session, finalize=receipt,
                            cancelled=cancelled)

    def operation_control(self, action, session_id, operation_id, expected_copy_sha256=None,
                          expected_native_sha256=None, cancelled=None):
        if action not in ("inspect", "adopt_current", "cancel_unsubmitted", "abandon_after_exit"):
            raise ValueError("Unknown recovery action")
        if not SESSION_ID.fullmatch(operation_id):
            raise ValueError("Invalid operation ID")
        lock = self._lock()
        pending = self.store / "pending.json"
        try:
            record = json.loads(pending.read_text())
            if (record.get("operation_id") != operation_id or record.get("session_id") != session_id
                    or record.get("workspace") != str(self.workspace)):
                raise ValueError("No matching unresolved operation")
            session = record.get("session")
            if not isinstance(session, dict):
                raise TypeError("This older operation has no retained session identity")
            state = self.status()
            same_process = state.get("running") and state.get("process") == session.get("process")
            if action in ("cancel_unsubmitted", "abandon_after_exit"):
                if action == "cancel_unsubmitted" and record.get("dispatched") is not False:
                    raise ValueError("Operation was dispatched; its outcome must be inspected")
                if action == "abandon_after_exit" and same_process:
                    raise ValueError("Original application process is still running")
                resolution = {"state": action, "operation_id": operation_id,
                              "session": session_id, "retry_safe": False}
                archive(self.store / ("recovered-" + operation_id + ".json"), record, resolution)
                session["state"] = "abandoned"
                write_json(self.store / (session_id + ".json"), session)
                pending.unlink()
                return resolution
            source = self._path(session["source"], exists=True)
            if digest(source) != session["source_sha256"]:
                raise ValueError("Source asset changed after submission")
            document = self._path(session["document"], suffix={".fla"}, exists=True)
            copy_hash = digest(document)
            receipt = completion(self.store / ("dispatch-" + operation_id) / "response.json", operation_id)
            observation = None
            if receipt is not None and same_process:
                observed = self._native({"version": 1, "operation": "inspect", "operation_id": uuid.uuid4().hex},
                                        cancelled=cancelled)["result"]
                matches = [doc for doc in observed.get("documents", []) if doc.get("path_uri") == document.as_uri()]
                if len(matches) == 1:
                    observation = matches[0]
                elif not matches and receipt.get("ok") and receipt.get("result", {}).get("document", {}).get("closed"):
                    observation = {"path_uri": document.as_uri(), "closed": True}
            native_hash = fingerprint(observation) if observation is not None else None
            result = {"state": "unresolved", "session": session_id, "operation_id": operation_id,
                      "completion_available": receipt is not None, "can_reconcile": observation is not None,
                      "copy_sha256": copy_hash, "native_sha256": native_hash,
                      "document": observation, "retry_safe": False}
            if action == "inspect":
                return result
            if observation is None:
                raise ValueError("Recovery requires a completed command and the original owned document")
            if expected_copy_sha256 != copy_hash or expected_native_sha256 != native_hash:
                raise ValueError("Copy or native observation changed; inspect recovery again")
            if digest(document) != copy_hash or digest(source) != session["source_sha256"]:
                raise AnimateUnknown("Recovery artifacts changed during inspection")
            session.update(document_sha256=copy_hash, state="closed" if observation.get("closed") else "owned")
            result.update(state="adopted_current")
            archive(self.store / ("recovered-" + operation_id + ".json"), record, result)
            write_json(self.store / (session_id + ".json"), session)
            pending.unlink()
            return result
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def close_copy(self, session_id, cancelled=None):
        session = self._session(session_id)
        document = self._path(session["document"], suffix={".fla"}, exists=True)
        def verify(result):
            doc = result.get("document", {})
            if doc.get("path_uri") != document.as_uri() or doc.get("closed") is not True:
                raise AnimateUnknown("Animate did not confirm the exact copy was closed")
            session["document_sha256"] = digest(document)
            session["state"] = "closed"
            write_json(self.store / (session_id + ".json"), session)
            return {"session": session_id, "document": doc}
        return self._mutate("close_copy", {"document_uri": document.as_uri()},
                            session=session, finalize=verify, cancelled=cancelled)

    def edit_timeline(self, session_id, edits, cancelled=None):
        edits = validate_timeline_edits(edits)
        session = self._session(session_id)
        document = self._path(session["document"], suffix={".fla"}, exists=True)
        snapshot = document.with_name(document.stem + "-before-edit-" + uuid.uuid4().hex + ".fla")
        self._path(str(snapshot.relative_to(self.workspace)))

        def verify(result):
            doc = result.get("document", {})
            if doc.get("path_uri") != document.as_uri():
                raise AnimateUnknown("Edited FLA document identity differs")
            before = snapshot_receipt(self.workspace, snapshot, result)
            session["document_sha256"] = digest(document)
            write_json(self.store / (session_id + ".json"), session)
            return {"session": session_id, "document": doc, "before_edit_snapshot": before,
                    "document_sha256": session["document_sha256"]}
        return self._mutate("edit_timeline", {"document_uri": document.as_uri(), "edits": edits,
                                               "snapshot_uri": snapshot.as_uri()},
                            session=session, finalize=verify, cancelled=cancelled)

    def set_stage_size_from_source(self, session_id, cancelled=None):
        session = self._session(session_id)
        document = self._path(session["document"], suffix={".fla"}, exists=True)
        source = self._path(session["source"], suffix={".png"}, exists=True)
        with Image.open(source) as image:
            if image.format != "PNG" or image.mode != "RGBA" or getattr(image, "n_frames", 1) != 1:
                raise AnimateRejected("Source must remain a single-frame RGBA PNG")
            width, height = image.size
            if not 1 <= width <= 2880 or not 1 <= height <= 2880:
                raise AnimateRejected("Source dimensions are outside Animate stage bounds")
        native = self.inspect()["native"]
        exact = [doc for doc in native.get("documents", []) if doc.get("path_uri") == document.as_uri()]
        if len(exact) != 1:
            raise AnimateRejected("The exact owned FLA must be open once in Animate", "DOCUMENT_IDENTITY_MISMATCH")
        snapshot_path = self._path(str(document.with_name(document.stem + "-before-stage-size.fla").relative_to(self.workspace)))
        def verify(result):
            doc = result.get("document", {})
            if doc.get("path_uri") != document.as_uri() or doc.get("width") != width or doc.get("height") != height:
                raise AnimateUnknown("Animate did not set and save the exact source-sized stage")
            snapshot = snapshot_receipt(self.workspace, snapshot_path, result)
            session["document_sha256"] = digest(document)
            write_json(self.store / (session_id + ".json"), session)
            return {"session": session_id, "document": doc, "source_sha256": session["source_sha256"],
                    "document_sha256": session["document_sha256"], "before_edit_snapshot": snapshot}
        return self._mutate("set_stage_size", {"document_uri": document.as_uri(), "width": width, "height": height,
                                                "snapshot_uri": snapshot_path.as_uri()},
                            session=session, finalize=verify, cancelled=cancelled)
