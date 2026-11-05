"""Bounded transport for the bundled Character Animator native adapter."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from cavi_mcp_core import apps
from cavi_mcp_core.contracts import OperationRejected, OperationUnknown

from . import native

BUNDLE_ID = "com.adobe.Character-Animator.application"
SOURCE = Path(__file__).with_name("character_native.swift")


class CharacterRejected(OperationRejected):
    """The native adapter rejected the request before an application mutation."""


class CharacterUnknown(OperationUnknown):
    """Application state must be inspected before another mutation."""


def application():
    """The Character Animator bundle and its Info.plist: the override, else the newest install."""
    if sys.platform != "darwin":
        raise CharacterRejected("Native Character Animator control requires macOS", "PLATFORM_UNSUPPORTED")
    try:
        app = apps.bundle("Adobe Character Animator", BUNDLE_ID, "Adobe Character Animator*/*.app",
                          "CAVI_CHARACTER_ANIMATOR_APP")
    except (apps.AppUnavailable, apps.AppMismatch) as exc:
        raise CharacterRejected(str(exc), exc.error_code) from exc
    return app.path, app.info


class NativeBridge:
    """Lazy helper build; server construction never builds or launches an app."""

    def __init__(self, directory, timeout=45):
        self.directory = Path(directory)
        self.timeout = timeout

    def executable(self):
        application()
        try:
            return native.build_helper(SOURCE, self.directory, "character-native")
        except native.HelperError as exc:
            raise CharacterRejected(str(exc), exc.code) from exc

    def call(self, operation, parameters=None, expected=None, cancelled=None):
        cancelled = cancelled or threading.Event()
        if cancelled.is_set():
            raise CharacterRejected("Request cancelled before native dispatch")
        path, _ = application()
        request_parameters = dict(parameters or {})
        attempt_id = uuid.uuid4().hex if operation == "continue_chooser" else None
        operation_id = request_parameters.get("operation_id") if attempt_id else None
        if attempt_id:
            request_parameters["native_trace"] = {
                "attempt_id": attempt_id,
                "operation_id": operation_id if isinstance(operation_id, str) else "",
            }
        request = {"version": 1, "operation": operation, "parameters": request_parameters,
                   "application": str(path)}
        if expected is not None:
            expected_keys = ["pid", "launch_time", "fingerprint"]
            if operation in ("focus", "continue_chooser"):
                expected_keys.extend(("bundle_id", "application", "project_roots"))
            request["expected"] = {key: expected[key] for key in expected_keys}
        binary = self.executable()
        if cancelled.is_set():
            raise CharacterRejected("Request cancelled before native dispatch")
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="character-native-") as directory:
            log = Path(directory) / "response.json"
            progress_log = None
            if attempt_id:
                progress_dir = self.directory / "character-native-attempts"
                progress_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                if progress_dir.is_symlink():
                    raise CharacterRejected("Native attempt directory must be a private real directory")
                progress_dir.chmod(0o700)
                safe_operation_id = (operation_id if isinstance(operation_id, str)
                                     and len(operation_id) == 32
                                     and all(char in "0123456789abcdef" for char in operation_id)
                                     else "unbound")
                progress_log = progress_dir / f"{safe_operation_id}-{attempt_id}.jsonl"
                descriptor = os.open(progress_log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(descriptor)
            with log.open("w+b") as output:
                progress_output = progress_log.open("ab", buffering=0) if progress_log else subprocess.DEVNULL
                process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=output,
                                           stderr=progress_output, start_new_session=True)
                try:
                    process.stdin.write(json.dumps(request).encode())
                    process.stdin.close()
                    while process.poll() is None:
                        if cancelled.is_set() or time.monotonic() - started > self.timeout or log.stat().st_size > 4 * 1024 * 1024:
                            failure = CharacterUnknown("Native request was interrupted; inspect the app and operation journal")
                            if progress_log:
                                failure.native_context = {
                                    "phase": "transport_interrupted",
                                    "submission_may_have_started": None,
                                    "attempt_id": attempt_id,
                                    "progress_path": str(progress_log),
                                    "progress": _read_progress(progress_log),
                                }
                            raise failure
                        time.sleep(.05)
                    if process.returncode or log.stat().st_size > 4 * 1024 * 1024:
                        raise CharacterUnknown("Native adapter did not return a bounded successful response")
                    output.seek(0)
                    try:
                        result = json.load(output)
                    except (ValueError, UnicodeError) as exc:
                        raise CharacterUnknown("Native adapter returned an invalid response") from exc
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)
                    if progress_log:
                        progress_output.close()
        if (not isinstance(result, dict) or result.get("version") != 1
                or result.get("operation") != operation or type(result.get("ok")) is not bool):
            raise CharacterUnknown("Native response identity differs from the request")
        if not result["ok"]:
            error = result.get("error", {})
            exception = CharacterRejected if error.get("outcome") == "rejected" else CharacterUnknown
            code = error.get("code", "OUTCOME_UNKNOWN")
            if (not isinstance(code, str) or not 1 <= len(code) <= 64
                    or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789" for c in code)):
                raise CharacterUnknown("Native adapter returned an invalid failure code")
            message = str(error.get("message", "Native operation failed"))
            if code == "STALE_STATE" and expected is not None and isinstance(result.get("state"), dict):
                differences = []

                def compare(left, right, path="state"):
                    if len(differences) >= 5 or left == right:
                        return
                    if isinstance(left, dict) and isinstance(right, dict):
                        for key in sorted(left.keys() | right.keys()):
                            if key != "fingerprint":
                                compare(left.get(key), right.get(key), path + "." + key)
                    elif isinstance(left, list) and isinstance(right, list):
                        if len(left) != len(right):
                            differences.append(f"{path}.length: {len(left)} -> {len(right)}")
                        else:
                            for index, (a, b) in enumerate(zip(left, right, strict=True)):
                                compare(a, b, f"{path}[{index}]")
                    else:
                        differences.append(f"{path}: {str(left)[:80]!r} -> {str(right)[:80]!r}")

                compare(expected, result["state"])
                message += "; changed native state: " + ("; ".join(differences) or "fingerprint only")
            failure = exception(message, code)
            if operation == "continue_chooser":
                diagnostics = error.get("continuation_diagnostics")
                failure.native_context = {
                    "phase": error.get("phase") if isinstance(error.get("phase"), str) else None,
                    "submission_may_have_started": (
                        error.get("submission_may_have_started")
                        if type(error.get("submission_may_have_started")) is bool else None
                    ),
                    "targeted_confirmation_may_have_started": (
                        error.get("targeted_confirmation_may_have_started")
                        if type(error.get("targeted_confirmation_may_have_started")) is bool else None
                    ),
                    "targeted_navigation_may_have_started": (
                        error.get("targeted_navigation_may_have_started")
                        if type(error.get("targeted_navigation_may_have_started")) is bool else None
                    ),
                    "continuation_diagnostics": diagnostics if isinstance(diagnostics, dict) else None,
                }
                if progress_log:
                    failure.native_context.update({
                        "attempt_id": attempt_id,
                        "progress_path": str(progress_log),
                        "progress": _read_progress(progress_log),
                    })
            raise failure
        if not isinstance(result.get("state"), dict) or not isinstance(result.get("result"), dict):
            raise CharacterUnknown("Native response lacks observed state")
        result["transport"] = {"source_sha256": binary.parent.name,
                               "helper_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
        if progress_log:
            progress_log.unlink(missing_ok=True)
        return result


def _read_progress(path):
    """Read only bounded, task-owned native attempt diagnostics."""
    try:
        if path.is_symlink() or path.stat().st_size > 64 * 1024:
            return ["progress file unavailable or exceeded its limit"]
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    return lines[-128:]
