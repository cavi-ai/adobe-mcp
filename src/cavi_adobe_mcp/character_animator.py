"""Cooperative Character Animator ownership and native-operation journals."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from cavi_mcp_core.journal import fingerprint, write_json

from .character_bridge import CharacterRejected, CharacterUnknown, NativeBridge

IDENTIFIER = re.compile(r"^[0-9a-f]{32}$")




def project_identity(state):
    """Only observed native identity; never invent a file path from a window title."""
    return fingerprint({key: state.get(key) for key in
                        ("bundle_id", "pid", "launch_time", "project_roots")})


class CharacterAnimator:
    def __init__(self, workspace, directory, bridge=None):
        self.workspace = Path(workspace).resolve(strict=True)
        self.directory = Path(directory) / fingerprint(str(self.workspace))[:16]
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.bridge = bridge or NativeBridge(Path(directory))

    @contextmanager
    def locked(self, cancelled=None):
        cancelled = cancelled or threading.Event()
        descriptor = os.open(self.directory / "session.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a") as stream:
            deadline = time.monotonic() + 15
            while True:
                if cancelled.is_set():
                    raise CharacterRejected("Cancelled before acquiring application ownership")
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise CharacterRejected("Another Character Animator request is still active")
                    time.sleep(.05)
            yield

    def read(self, name):
        path = self.directory / name
        if not path.exists():
            return None
        if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
            raise CharacterUnknown("Private operation state is not a bounded regular file")
        return json.loads(path.read_text())

    def inspect(self, cancelled=None):
        observed = self.bridge.call("inspect", cancelled=cancelled)
        state = observed["state"]
        pending = self.read("operation.json")
        return {"project_id": project_identity(state), "native": observed,
                "pending_operation": pending, "launches_application": False}

    def session(self, action, owner=None, expected_project=None, lease_id=None,
                ttl_seconds=600, cancelled=None, pending_operation_id=None):
        if action not in ("inspect", "claim", "renew", "release", "resume"):
            raise CharacterRejected("Choose inspect, claim, renew, release or resume")
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 3600:
            raise CharacterRejected("Lease lifetime must be 30..3600 seconds")
        with self.locked(cancelled):
            observed = self.inspect(cancelled)
            identity = observed["project_id"]
            lease = self.read("lease.json")
            active = lease is not None and lease["expires_at"] > time.time()
            if action == "claim":
                if not isinstance(observed["native"]["state"].get("pid"), int):
                    raise CharacterRejected("Start Character Animator and inspect its native state first")
                if (not isinstance(owner, str) or not 1 <= len(owner) <= 128
                        or any(ord(c) < 32 for c in owner) or expected_project != identity):
                    raise CharacterRejected("Claim requires an owner and the current observed project_id")
                if active or observed["pending_operation"]:
                    raise CharacterRejected("An active lease or unresolved operation already owns this application")
                lease = {"lease_id": uuid.uuid4().hex, "owner": owner, "project_id": identity,
                         "expires_at": time.time() + ttl_seconds, "owned_root": None}
                write_json(self.directory / "lease.json", lease)
            elif action == "resume":
                packet = observed["pending_operation"]
                if (lease is None or active or lease_id != lease.get("lease_id")
                        or owner != lease.get("owner") or not isinstance(owner, str)
                        or not packet or packet.get("state") != "unknown"
                        or packet.get("operation_id") != pending_operation_id
                        or expected_project != identity or expected_project != lease.get("project_id")
                        or packet.get("before_project_id") != identity):
                    raise CharacterRejected("Resume requires the expired original lease, owner, project and exact unknown operation")
                original_observation = packet.get("native") or packet.get("before_native")
                original_state = original_observation.get("state") if isinstance(original_observation, dict) else None
                owned_root = lease.get("owned_root")
                if "owned_root" not in lease:
                    raise CharacterRejected("Resume requires the original lease ownership state")
                if owned_root is None:
                    source, _, _ = self._initial_copy_paths(packet)
                    expected_root = str(source)
                else:
                    expected_root = owned_root
                state = observed["native"]["state"]
                if (not isinstance(original_state, dict) or not isinstance(expected_root, str)
                        or packet.get("parameters", {}).get("project_root") != expected_root
                        or state.get("project_roots") != [expected_root]
                        or (owned_root is None and
                            (original_state.get("project_roots") != [expected_root]
                             or project_identity(original_state) != packet.get("before_project_id")))
                        or state.get("authentication_or_permission_prompt") is True
                        or (state.get("pid"), state.get("launch_time"))
                        != (original_state.get("pid"), original_state.get("launch_time"))):
                    raise CharacterRejected("Resume requires the same recorded process and sole owned project root")
                lease = {**lease, "lease_id": uuid.uuid4().hex,
                         "project_id": identity, "expires_at": time.time() + ttl_seconds}
                write_json(self.directory / "lease.json", lease)
            elif action in ("renew", "release"):
                if (not active or lease_id != lease["lease_id"] or identity != lease["project_id"]):
                    raise CharacterRejected("Lease is expired, stale or owned by another caller")
                if action == "release":
                    if observed["pending_operation"]:
                        raise CharacterRejected("Inspect and resolve the pending operation before releasing ownership")
                    (self.directory / "lease.json").unlink()
                    lease = None
                else:
                    lease["expires_at"] = time.time() + ttl_seconds
                    write_json(self.directory / "lease.json", lease)
            result = {"project_id": identity, "owner": lease["owner"] if lease else None,
                      "expires_at": lease["expires_at"] if lease else None,
                      "state": "owned" if lease and lease["expires_at"] > time.time() else "unclaimed",
                      "pending_operation": observed["pending_operation"]}
            if action in ("claim", "renew", "resume"):
                result["lease_id"] = lease["lease_id"]
            return result

    def path(self, value, *, new=False):
        if (not isinstance(value, str) or not value or any(ord(c) < 32 for c in value)
                or ".." in Path(value).parts):
            raise CharacterRejected("Use a workspace path without traversal or control characters")
        logical = self.workspace / value
        path = logical.resolve()
        if logical != path or not path.is_relative_to(self.workspace) or path == self.workspace:
            raise CharacterRejected("Paths must stay inside the shared workspace without symlinks")
        if new and path.exists():
            raise CharacterRejected("Output must not already exist")
        return path

    def _initial_copy_paths(self, packet):
        parameters, intent = packet.get("parameters"), packet.get("intent")
        source_root = parameters.get("project_root") if isinstance(parameters, dict) else None
        if (packet.get("operation") != "save_as"
                or packet.get("initial_native_operation") != "save_as"
                or not isinstance(intent, dict)):
            raise CharacterRejected("This operation is not a recorded initial Save As copy")
        name = intent.get("name")
        if not isinstance(name, str) or not name or Path(name).name != name or name in (".", ".."):
            raise CharacterRejected("Initial Save As requires its recorded copy name")
        if not isinstance(source_root, str) or not Path(source_root).is_absolute():
            raise CharacterRejected("Initial Save As requires its recorded absolute source root")
        source = Path(source_root)
        try:
            canonical_source = source.resolve(strict=True)
        except OSError as exc:
            raise CharacterRejected("Initial Save As source root is unavailable") from exc
        directory = self.path(intent.get("directory"))
        destination = self.path(intent.get("destination"), new=True)
        expected_destination = self.path(str(directory / name), new=True)
        if (canonical_source != source or not source.is_dir()
                or str(directory) != intent.get("directory") or not directory.is_dir()
                or str(destination) != intent.get("destination")
                or destination != expected_destination or destination == source):
            raise CharacterRejected("Initial Save As requires the exact new workspace copy destination")
        return source, directory, destination

    @staticmethod
    def _chooser_visible(state, kind, allow_go_to):
        titles = {"save_as": ("save-panel", "Save Project As"),
                  "import": ("open-panel", "Import"),
                  "export_png_wav": ("save-panel", "Export Scene"),
                  "open_project": ("open-panel", "Open Project")}
        panel = titles.get(kind)
        if panel is None:
            return False
        focused = state.get("focused_window") or {}
        if focused.get("identifier") == panel[0] and focused.get("title") == panel[1]:
            return True
        return (allow_go_to and focused.get("identifier") == "GoToWindow"
                and focused.get("role") == "AXSheet"
                and any(window.get("identifier") == panel[0] and window.get("title") == panel[1]
                        for window in state.get("windows", []) if isinstance(window, dict)))

    def mutate(self, operation, parameters, lease_id, *, owned=True, cancelled=None,
               continuation=None, intent=None, expected_fingerprint=None,
               adopt_existing_import=False):
        """Record uncertainty before dispatch; no implicit retry or journal clearing."""
        with self.locked(cancelled):
            before = self.inspect(cancelled)
            lease = self.read("lease.json")
            if (lease is None or lease_id != lease["lease_id"] or lease["expires_at"] <= time.time()
                    or lease["project_id"] != before["project_id"]):
                raise CharacterRejected("A current matching lease is required")
            if before["pending_operation"]:
                raise CharacterRejected("An unresolved native command blocks further mutations")
            if (expected_fingerprint is not None
                    and before["native"]["state"].get("fingerprint") != expected_fingerprint):
                raise CharacterRejected("Observed selection or native state changed; inspect again before acting")
            if owned and (not lease.get("owned_root") or lease["owned_root"] not in before["native"]["state"].get("project_roots", [])):
                raise CharacterRejected("First save a new owned project copy and verify its native identity")
            operation_id = uuid.uuid4().hex
            roots = before["native"]["state"].get("project_roots", [])
            if len(roots) != 1:
                raise CharacterRejected("Exactly one observed native project root is required")
            parameters = {**parameters, "project_root": roots[0]}
            initial_native_operation = operation
            if operation == "import" and adopt_existing_import:
                state = before["native"]["state"]
                focus = state.get("focused_window") or {}
                windows = state.get("windows", [])
                exact_open_panel = (focus.get("identifier") == "open-panel"
                                    and focus.get("title") == "Import"
                                    and focus.get("modal") is True)
                import_parent = any(window.get("identifier") == "open-panel"
                                    and window.get("title") == "Import" for window in windows)
                exact_go_to = (focus.get("identifier") == "GoToWindow"
                               and focus.get("role") == "AXSheet" and import_parent)
                looks_like_import_chooser = (focus.get("identifier") == "GoToWindow"
                                             or (focus.get("identifier") == "open-panel"
                                                 and focus.get("title") == "Import"))
                modal_windows = [window for window in windows if window.get("modal") is True]
                allowed_modals = all(
                    (window.get("identifier") == "open-panel" and window.get("title") == "Import")
                    or window.get("identifier") == "GoToWindow" for window in modal_windows)
                if ((focus.get("modal") is True and not (exact_open_panel or exact_go_to))
                        or (modal_windows and (not allowed_modals or not (exact_open_panel or exact_go_to)))
                        or (looks_like_import_chooser and not (exact_open_panel or exact_go_to))):
                    raise CharacterRejected("An unrelated native modal blocks import")
                if exact_open_panel or exact_go_to:
                    initial_native_operation = "inspect_existing_import"
            packet = {"version": 1, "operation_id": operation_id, "operation": operation,
                      "parameters": parameters, "before_project_id": before["project_id"],
                      "before_native": before["native"],
                      "initial_native_operation": initial_native_operation,
                      "intent": intent,
                      "created_at": time.time(), "state": "pending", "retry_safe": False}
            journal = self.directory / "operation.json"
            if cancelled is not None and cancelled.is_set():
                raise CharacterRejected("Cancelled before native dispatch")
            write_json(journal, packet)
            dispatched = 0
            try:
                native_command = "inspect" if initial_native_operation == "inspect_existing_import" else operation
                native = (self.bridge.call("inspect", cancelled=cancelled) if native_command == "inspect"
                          else self.bridge.call(native_command, parameters,
                                               expected=before["native"]["state"], cancelled=cancelled))
                dispatched += 1
                packet.update(state="observed", native=native)
                write_json(journal, packet)
                if continuation:
                    def step(command, arguments):
                        nonlocal native, dispatched
                        current = self.bridge.call("inspect", cancelled=cancelled)
                        if project_identity(current["state"]) != project_identity(native["state"]):
                            raise CharacterUnknown("Native project changed between workflow steps")
                        if command in ("chooser_destination", "chooser_continue_directory"):
                            kind = arguments.get("kind")
                            allow_go_to = command == "chooser_continue_directory"
                            if not self._chooser_visible(current["state"], kind, allow_go_to):
                                state = current["state"]
                                focused = state.get("focused_window") or {}
                                main_window = (state.get("frontmost") is False
                                               and focused.get("role") == "AXLayoutArea"
                                               and focused.get("title") == "Character Animator")
                                modal = (focused.get("modal") is True
                                         or any(window.get("modal") is True or window.get("role") == "AXSheet"
                                                for window in state.get("windows", [])
                                                if isinstance(window, dict)))
                                if (not main_window or modal
                                        or state.get("authentication_or_permission_prompt") is True):
                                    raise CharacterRejected("Native chooser is unavailable and the app is not a safe background main window")
                                activated = self.bridge.call(
                                    "focus", {"kind": kind, "project_root": arguments.get("project_root")},
                                    expected=state, cancelled=cancelled)
                                activated_state = activated.get("state", {})
                                if (project_identity(activated_state) != project_identity(native["state"])
                                        or activated_state.get("project_roots") != [arguments.get("project_root")]
                                        or (activated_state.get("pid"), activated_state.get("launch_time"))
                                        != (state.get("pid"), state.get("launch_time"))
                                        or activated_state.get("frontmost") is not True
                                        or activated_state.get("authentication_or_permission_prompt") is True
                                        or not self._chooser_visible(activated_state, kind, allow_go_to=True)):
                                    raise CharacterRejected("Activation did not expose the exact same-process native chooser")
                                current = activated
                                previous = activated_state.get("fingerprint")
                            deadline = time.monotonic() + 5
                            previous = current["state"].get("fingerprint")
                            stable = False
                            while time.monotonic() < deadline:
                                time.sleep(.1)
                                settled = self.bridge.call("inspect", cancelled=cancelled)
                                if project_identity(settled["state"]) != project_identity(native["state"]):
                                    raise CharacterUnknown("Native project changed while the Save As panel settled")
                                fingerprint = settled["state"].get("fingerprint")
                                if (self._chooser_visible(settled["state"], kind, allow_go_to)
                                        and fingerprint == previous):
                                    current = settled
                                    stable = True
                                    break
                                previous = fingerprint
                                current = settled
                            if not stable:
                                raise CharacterUnknown("Save As panel did not reach a stable observed state")
                        native = self.bridge.call(command, arguments, expected=current["state"], cancelled=cancelled)
                        dispatched += 1
                        packet.update(native=native, last_command=command)
                        write_json(journal, packet)
                        return native
                    result = continuation(step, native, lease)
                    packet.update(state="completed", result=result)
                    receipts = self.directory / "operations"
                    receipts.mkdir(exist_ok=True, mode=0o700)
                    write_json(receipts / (operation_id + ".json"), packet)
                    write_json(self.directory / "lease.json", lease)
                    journal.unlink()
                    return {**result, "operation_id": operation_id, "retry_safe": False}
                return {"operation_id": operation_id, "status": "native_command_dispatched",
                        "completion": "unverified", "native": native, "retry_safe": False}
            except BaseException as exc:
                if isinstance(exc, CharacterRejected) and dispatched == 0:
                    journal.unlink()
                    raise
                packet["state"] = "unknown"
                packet["failure"] = {"code": getattr(exc, "error_code", "OUTCOME_UNKNOWN"),
                                     "message": str(exc)[:4000]}
                write_json(journal, packet)
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                raise CharacterUnknown("Native workflow did not finish; inspect its journal. "
                                       + packet["failure"]["code"] + ": " + str(exc)[:1000]) from exc

    def save_copy(self, directory, name, lease_id, cancelled=None):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
            raise CharacterRejected("Project name must be a safe ASCII name of at most 64 characters")
        parent = self.path(directory)
        if not parent.is_dir():
            raise CharacterRejected("Destination parent directory must already exist")
        destination = self.path(str(parent / name), new=True)

        def finish(step, native, lease):
            roots = native["state"].get("project_roots", [])
            if len(roots) != 1:
                raise CharacterUnknown("Native source project identity is ambiguous")
            step("chooser_destination", {"kind": "save_as", "directory": str(parent),
                                         "basename": name, "project_root": roots[0]})
            arguments = {"kind": "save_as", "directory": str(parent),
                         "basename": name, "project_root": roots[0]}
            directory = step("chooser_continue_directory", arguments)
            if directory["result"]["status"] == "native_directory_requested":
                step("chooser_continue_directory", arguments)
            deadline = time.monotonic() + 15
            while True:
                if cancelled is not None and cancelled.is_set():
                    raise CharacterUnknown("Copy request interrupted; inspect its destination")
                observed = self.bridge.call("inspect", cancelled=cancelled)
                if str(destination) in observed["state"].get("project_roots", []):
                    break
                if time.monotonic() >= deadline:
                    raise CharacterUnknown("Saved copy is not the observed native project")
                time.sleep(.1)
            files = list(destination.glob("*.chproj"))
            if len(files) != 1 or not (destination / "Ch Data").is_dir():
                raise CharacterUnknown("Native copy lacks its project file or project data")
            refs = self.project_files(destination)
            lease.update(project_id=project_identity(observed["state"]), owned_root=str(destination))
            return {"status": "owned_copy", "project_id": lease["project_id"],
                    "project_root": str(destination), "project_file": str(files[0]),
                    "files": refs, "original_source_hash": None,
                    "source_identity": "observed_native_project_root", "native": observed}

        return self.mutate("save_as", {}, lease_id, owned=False, cancelled=cancelled, continuation=finish,
                           intent={"destination": str(destination), "directory": str(parent), "name": name})

    @staticmethod
    def _select_import_file(step, arguments, start_at):
        if start_at == "go_to_folder":
            selected = step("chooser_continue_directory", arguments)
        elif start_at == "import_panel":
            selected = step("chooser_destination", arguments)
        else:
            raise CharacterRejected("Import continuation requires an observed Import panel or Go To sheet")
        for _ in range(2):
            if selected["result"].get("status") not in (
                    "native_chooser_directory_requested", "native_directory_requested"):
                break
            selected = step("chooser_continue_directory", arguments)
        if selected["result"].get("status") != "native_chooser_submitted":
            raise CharacterUnknown("Native import chooser was not submitted; inspect the pending operation")
        return selected

    def import_media(self, source, kind, lease_id, cancelled=None):
        """Import one immutable workspace PSD/WAV through the observed native file chooser."""
        extensions = {"puppet_psd": ".psd", "audio_wav": ".wav"}
        if kind not in extensions:
            raise CharacterRejected("Choose puppet_psd or audio_wav")
        original = self.path(source)
        if original.suffix.lower() != extensions[kind] or not original.is_file():
            raise CharacterRejected("Import source must be a regular workspace PSD or WAV matching its kind")
        before = original.stat()
        if before.st_size <= 0 or before.st_size > 1024 * 1024 * 1024:
            raise CharacterRejected("Import source must be non-empty and at most 1 GiB")
        with original.open("rb") as stream:
            source_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if (original.stat().st_dev, original.stat().st_ino, original.stat().st_size,
                original.stat().st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise CharacterRejected("Import source changed while it was being hashed")
        inputs = self.directory / "inputs"
        inputs.mkdir(exist_ok=True, mode=0o700)
        import_directory = inputs / uuid.uuid4().hex
        import_directory.mkdir(mode=0o700)
        staged = import_directory / original.name
        with original.open("rb") as src, staged.open("xb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        staged.chmod(0o400)
        with staged.open("rb") as stream:
            staged_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        with original.open("rb") as stream:
            after_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if staged_digest != source_digest or after_digest != source_digest:
            raise CharacterRejected("Import source changed while its private copy was created")

        def finish(step, native, lease):
            roots = native["state"].get("project_roots", [])
            if len(roots) != 1 or roots[0] != lease.get("owned_root"):
                raise CharacterUnknown("Import requires the exact owned project root")
            arguments = {"kind": "import", "directory": str(staged.parent),
                         "basename": staged.name, "project_root": roots[0]}
            focus = native["state"].get("focused_window") or {}
            start_at = "go_to_folder" if focus.get("identifier") == "GoToWindow" else "import_panel"
            selected = self._select_import_file(step, arguments, start_at)
            after = self.bridge.call("inspect", cancelled=cancelled)
            if project_identity(after["state"]) != project_identity(selected["state"]):
                raise CharacterUnknown("Character Animator project changed after import selection")
            with staged.open("rb") as stream:
                current_staged_digest = hashlib.file_digest(stream, "sha256").hexdigest()
            with original.open("rb") as stream:
                current_source_digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if current_staged_digest != source_digest or current_source_digest != source_digest:
                raise CharacterUnknown("The import source or its retained copy changed during native selection")
            return {"status": "native_import_submitted", "completion": "unverified",
                    "kind": kind, "source": {"path": str(original), "sha256": source_digest,
                                               "bytes": before.st_size},
                    "import_copy": {"path": str(staged), "sha256": staged_digest,
                                    "bytes": staged.stat().st_size},
                    "native": after, "source_identity": "hashed_private_import_copy"}

        return self.mutate("import", {}, lease_id, cancelled=cancelled, continuation=finish,
                           adopt_existing_import=True,
                           intent={"source": str(original), "source_sha256": source_digest,
                                   "source_bytes": before.st_size,
                                   "import_copy": str(staged), "import_copy_sha256": staged_digest,
                                   "import_copy_bytes": staged.stat().st_size,
                                   "import_directory_id": import_directory.name,
                                   "kind": kind})

    def scene_action(self, action, expected_fingerprint, lease_id, cancelled=None):
        """Dispatch one exact observed menu action against an owned project's current selection."""
        actions = {"add_to_new_scene", "create_take_from_scene_audio", "compute_lip_sync",
                   "export_png_wav"}
        if action not in actions:
            raise CharacterRejected("Choose a supported fixed scene action")
        if not isinstance(expected_fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_fingerprint):
            raise CharacterRejected("Use the 64-character state fingerprint from the latest inspect")

        def finish(_step, native, lease):
            before_state = native.get("state", {})
            if project_identity(before_state) != lease["project_id"]:
                raise CharacterUnknown("Native menu dispatch changed the owned process or project")
            after = self.bridge.call("inspect", cancelled=cancelled)
            after_state = after["state"]
            if (project_identity(after_state) != lease["project_id"]
                    or after_state.get("authentication_or_permission_prompt") is True):
                raise CharacterUnknown("Scene action outcome requires inspection; project identity or prompt changed")
            return {"status": "native_scene_action_submitted", "action": action,
                    "completion": "unverified", "expected_state_fingerprint": expected_fingerprint,
                    "native_dispatch": native, "native_after": after,
                    "selection_identity": "caller_observed_state_fingerprint"}

        return self.mutate(action, {}, lease_id, cancelled=cancelled, continuation=finish,
                           intent={"action": action, "expected_state_fingerprint": expected_fingerprint},
                           expected_fingerprint=expected_fingerprint)

    def scene_export(self, directory, name, expected_fingerprint, lease_id, cancelled=None):
        """Submit one selected scene's PNG/WAV export into a new workspace directory."""
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,47}", name):
            raise CharacterRejected("Export name must be a safe ASCII basename of at most 48 characters")
        target = self.path(directory, new=True)
        if not target.parent.is_dir():
            raise CharacterRejected("Export directory parent must already exist")
        if not isinstance(expected_fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_fingerprint):
            raise CharacterRejected("Use the 64-character state fingerprint from the latest inspect")
        intent = {"directory": str(target), "name": name,
                  "expected_state_fingerprint": expected_fingerprint}

        def finish(step, native, lease):
            source_root = lease.get("owned_root")
            if (not isinstance(source_root, str)
                    or native["state"].get("project_roots") != [source_root]
                    or project_identity(native["state"]) != lease["project_id"]
                    or native["state"].get("authentication_or_permission_prompt") is True):
                raise CharacterUnknown("Export requires the same owned project process without a prompt")
            target.mkdir(mode=0o700, exist_ok=False)
            target_stat = target.stat()
            intent["output_directory_identity"] = {"device": target_stat.st_dev,
                                                    "inode": target_stat.st_ino}
            pending = self.read("operation.json")
            if not pending or pending.get("operation") != "export_png_wav":
                raise CharacterUnknown("Export output directory lacks its pending operation journal")
            pending["intent"] = intent
            write_json(self.directory / "operation.json", pending)
            arguments = {"kind": "export_png_wav", "directory": str(target),
                         "basename": name, "project_root": source_root}
            selected = step("chooser_destination", arguments)
            for _ in range(3):
                if selected.get("result", {}).get("status") not in (
                        "native_chooser_directory_requested", "native_directory_requested"):
                    break
                selected = step("chooser_continue_directory", arguments)
            if selected.get("result", {}).get("status") != "native_chooser_submitted":
                raise CharacterUnknown("Native export chooser was not submitted")
            after = self.bridge.call("inspect", cancelled=cancelled)
            if (project_identity(after["state"]) != lease["project_id"]
                    or after["state"].get("project_roots") != [source_root]
                    or after["state"].get("authentication_or_permission_prompt") is True):
                raise CharacterUnknown("Export submission changed the owned project or exposed a prompt")
            output_dir = target / name
            return {"status": "native_export_submitted", "completion": "unverified",
                    "directory": str(target), "name": name,
                    "expected_output_directory": str(output_dir),
                    "expected_xml": str(target / (name + ".xml")),
                    "native_submission": selected, "native": after,
                    "output_verification": "use export_inspect after PNG/WAV files appear"}

        return self.mutate("export_png_wav", {}, lease_id, cancelled=cancelled,
                           continuation=finish,
                           intent=intent,
                           expected_fingerprint=expected_fingerprint)

    def project_select(self, name, item_type, extend, expected_fingerprint, lease_id, cancelled=None):
        """Select one OCR-identified Project-panel item in the exact inspected state."""
        if item_type not in ("puppet", "audio", "scene"):
            raise CharacterRejected("item_type must be puppet, audio or scene")
        if (not isinstance(name, str) or not 1 <= len(name) <= 128
                or any(ord(character) < 32 for character in name)):
            raise CharacterRejected("Project item name must contain 1..128 printable characters")
        if type(extend) is not bool:
            raise CharacterRejected("extend must be a strict boolean")
        if not isinstance(expected_fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_fingerprint):
            raise CharacterRejected("Use the 64-character state fingerprint from the latest inspect")

        def finish(_step, native, lease):
            evidence = native.get("result", {}).get("selection_evidence")
            if (native.get("result", {}).get("status") != "native_project_item_selected"
                    or not isinstance(evidence, dict)
                    or project_identity(native.get("state", {})) != lease["project_id"]):
                raise CharacterUnknown("Project item selection lacks exact native result or owned project identity")
            after = self.bridge.call("inspect", cancelled=cancelled)
            if (project_identity(after["state"]) != lease["project_id"]
                    or after["state"].get("authentication_or_permission_prompt") is True):
                raise CharacterUnknown("Project selection outcome requires inspection; project identity or prompt changed")
            return {"status": "native_project_item_selected", "name": name, "item_type": item_type,
                    "extend": extend, "expected_state_fingerprint": expected_fingerprint,
                    "selection_evidence": evidence,
                    "native": after, "completion": "unverified"}

        return self.mutate("project_select", {"name": name, "item_type": item_type,
                                              "extend": extend}, lease_id, cancelled=cancelled,
                           continuation=finish,
                           intent={"name": name, "item_type": item_type, "extend": extend,
                                   "expected_state_fingerprint": expected_fingerprint},
                           expected_fingerprint=expected_fingerprint)

    def project_files(self, directory):
        root = self.path(str(directory))
        result, total = [], 0
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise CharacterUnknown("Native project contains an untracked symlink")
            if not path.is_file() or path.name == "interprocess.lock":
                continue
            before = path.stat()
            total += before.st_size
            if len(result) >= 4096 or total > 1024 * 1024 * 1024:
                raise CharacterUnknown("Native project exceeds the copy receipt budget")
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            after = path.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise CharacterUnknown("Native project is still changing; inspect it before proceeding")
            result.append({"path": str(path.relative_to(self.workspace)), "sha256": digest,
                           "bytes": before.st_size})
        return result

    def capture(self, directory, cancelled=None):
        target = self.path(directory, new=True)
        target.mkdir(parents=True, mode=0o700)
        output = target / "character.png"
        native = self.bridge.call("capture", {"destination": str(output)}, cancelled=cancelled)
        try:
            if output.is_symlink() or output.stat().st_size > 32 * 1024 * 1024:
                raise CharacterUnknown("Native capture is not a bounded regular artifact")
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            if digest != native["result"].get("sha256"):
                raise CharacterUnknown("Native capture differs from its receipt")
            return {"path": str(output), "sha256": digest, "native": native,
                    "identity": "observed_window_and_ocr", "launches_application": False}
        except (OSError, ValueError, TypeError) as exc:
            raise CharacterUnknown("Capture started; inspect its output directory") from exc

    def operation_control(self, action, operation_id, lease_id=None, cancelled=None,
                          user_confirmed_cancelled=False):
        if action not in ("inspect", "verify_copy", "continue_copy", "continue_import", "continue_export",
                          "confirm_cancelled_import", "cancel_unsubmitted",
                          "reconcile_unsubmitted_copy") or not IDENTIFIER.fullmatch(operation_id):
            raise CharacterRejected("Use a known operation ID and a supported operation control action")
        if action == "confirm_cancelled_import" and type(user_confirmed_cancelled) is not bool:
            raise CharacterRejected("user_confirmed_cancelled must be a strict boolean")
        if action == "confirm_cancelled_import" and not user_confirmed_cancelled:
            raise CharacterRejected("Confirm only after the user reports that the import was cancelled")
        with self.locked(cancelled):
            packet = self.read("operation.json")
            if not packet or packet["operation_id"] != operation_id:
                raise CharacterRejected("No matching unresolved operation")
            observed = self.bridge.call("inspect", cancelled=cancelled)
            if action == "inspect":
                return {"status": packet["state"], "operation_id": operation_id,
                        "operation": packet, "native": observed, "retry_safe": False}
            lease = self.read("lease.json")
            required_operation = ("import" if action in ("continue_import", "confirm_cancelled_import")
                                  else "export_png_wav" if action == "continue_export" else "save_as")
            allowed_operations = ({"save_as", "export_png_wav"} if action == "cancel_unsubmitted"
                                  else {required_operation})
            if (not lease or lease_id != lease["lease_id"] or lease["expires_at"] <= time.time()
                    or packet["operation"] not in allowed_operations or packet.get("state") != "unknown"):
                raise CharacterRejected("The original lease and matching unresolved operation are required")
            original_observation = packet.get("native") or packet.get("before_native")
            if not isinstance(original_observation, dict) or not isinstance(original_observation.get("state"), dict):
                raise CharacterRejected("Pending operation has no recorded native observation to verify against")
            original = original_observation["state"]
            state = observed["state"]
            if (state.get("pid"), state.get("launch_time")) != (original.get("pid"), original.get("launch_time")):
                raise CharacterRejected("The native application process changed; outcome remains unresolved")
            if action == "reconcile_unsubmitted_copy":
                source, _, destination = self._initial_copy_paths(packet)
                source_root = str(source)
                recorded_native = packet.get("native")
                recorded_result = recorded_native.get("result") if isinstance(recorded_native, dict) else None
                recorded_state = recorded_native.get("state") if isinstance(recorded_native, dict) else None
                before_native = packet.get("before_native")
                before_state = before_native.get("state") if isinstance(before_native, dict) else None

                def has_modal_or_chooser(native_state):
                    focus = native_state.get("focused_window") or {}
                    chooser_ids = {"save-panel", "GoToWindow", "open-panel"}
                    chooser_titles = {"Save Project As", "Export Scene", "Import"}
                    windows = native_state.get("windows", [])
                    return (native_state.get("authentication_or_permission_prompt") is True
                            or focus.get("modal") is True or focus.get("role") == "AXSheet"
                            or focus.get("identifier") in chooser_ids
                            or focus.get("title") in chooser_titles
                            or any(window.get("modal") is True or window.get("role") == "AXSheet"
                                   or window.get("identifier") in chooser_ids
                                   or window.get("title") in chooser_titles
                                   for window in windows if isinstance(window, dict)))

                if (lease.get("owned_root") is not None
                        or lease.get("project_id") != packet.get("before_project_id")
                        or packet.get("last_command") != "chooser_destination"
                        or not isinstance(recorded_native, dict)
                        or recorded_native.get("operation") != "chooser_destination"
                        or not isinstance(recorded_result, dict)
                        or recorded_result.get("status") != "native_chooser_directory_requested"
                        or not isinstance(original, dict)
                        or not isinstance(original.get("pid"), int)
                        or original.get("launch_time") is None
                        or not isinstance(recorded_state, dict)
                        or recorded_state.get("project_roots") != [source_root]
                        or project_identity(recorded_state) != packet.get("before_project_id")
                        or not isinstance(before_state, dict)
                        or before_state.get("project_roots") != [source_root]
                        or project_identity(before_state) != packet.get("before_project_id")
                        or project_identity(state) != packet.get("before_project_id")
                        or state.get("project_roots") != [source_root]
                        or has_modal_or_chooser(state)):
                    raise CharacterRejected("Reconcile requires the recorded pre-submit Save As stage and the same unobstructed source project")
                first_fingerprint = state.get("fingerprint")
                fresh = self.bridge.call("inspect", cancelled=cancelled)
                fresh_state = fresh.get("state", {})
                if (not isinstance(first_fingerprint, str) or not first_fingerprint
                        or fresh_state.get("fingerprint") != first_fingerprint
                        or project_identity(fresh_state) != packet.get("before_project_id")
                        or fresh_state.get("project_roots") != [source_root]
                        or (fresh_state.get("pid"), fresh_state.get("launch_time"))
                        != (original.get("pid"), original.get("launch_time"))
                        or has_modal_or_chooser(fresh_state) or destination.exists()):
                    raise CharacterRejected("Reconcile requires a stable no-chooser observation and absent copy destination")
                result = {"status": "unsubmitted_copy_reconciled",
                          "completion": "recorded_pre_submit_stage_closed_without_submission",
                          "expected_output_exists": False, "native": fresh}
                packet.update(state="resolved", resolution="reconcile_unsubmitted_copy",
                              reconciliation_basis="recorded_pre_submit_directory_request",
                              reconciliation_native=fresh, result=result)
                receipts = self.directory / "operations"
                receipts.mkdir(exist_ok=True, mode=0o700)
                receipt = receipts / (operation_id + ".json")
                if receipt.exists():
                    raise CharacterRejected("A reconciliation receipt already exists for this operation")
                try:
                    write_json(receipt, packet)
                    write_json(self.directory / "lease.json", lease)
                    (self.directory / "operation.json").unlink()
                except OSError as exc:
                    raise CharacterUnknown("Unsubmitted stage was observed but its reconciliation journal was not committed") from exc
                return {**result, "operation_id": operation_id, "retry_safe": False}
            if action == "continue_export":
                intent = packet.get("intent", {})
                target = self.path(intent.get("directory"))
                identity = intent.get("output_directory_identity")
                target_stat = target.stat() if target.is_dir() and not target.is_symlink() else None
                source_root = packet.get("parameters", {}).get("project_root")
                if (not isinstance(identity, dict) or target_stat is None
                        or {"device": target_stat.st_dev, "inode": target_stat.st_ino} != identity
                        or project_identity(state) != packet.get("before_project_id")
                        or state.get("project_roots") != [source_root]
                        or lease.get("owned_root") != source_root
                        or state.get("authentication_or_permission_prompt") is True):
                    raise CharacterRejected("Export continuation requires the same journaled directory and owned project")
                recorded_native = packet.get("native")
                recorded_result = recorded_native.get("result") if isinstance(recorded_native, dict) else None
                last_command = packet.get("last_command")
                if (not isinstance(recorded_native, dict)
                        or (last_command is not None and recorded_native.get("operation") != last_command)
                        or not isinstance(recorded_result, dict)):
                    raise CharacterRejected("Export continuation has no recorded chooser stage to resume")
                if (last_command == "chooser_destination"
                        and recorded_result.get("status") == "native_chooser_directory_requested"):
                    resume_phase = "go_to"
                elif (last_command == "chooser_continue_directory"
                      and recorded_result.get("status") == "native_directory_requested"):
                    resume_phase = "export_panel"
                elif (last_command is None
                      and recorded_native.get("operation") == "export_png_wav"
                      and recorded_result.get("status") == "native_command_dispatched"):
                    resume_phase = "initial_menu"
                else:
                    raise CharacterRejected("Export continuation requires a recorded unsubmitted directory stage")
                focus = state.get("focused_window") or {}
                def export_phase(native_state):
                    focused = native_state.get("focused_window") or {}
                    if (focused.get("identifier") == "save-panel"
                            and focused.get("title") == "Export Scene"):
                        return "export_panel"
                    if self._chooser_visible(native_state, "export_png_wav", allow_go_to=True):
                        return "go_to"
                    return None

                current_phase = export_phase(state)
                if current_phase is None or state.get("frontmost") is not True:
                    main_window = (state.get("frontmost") is False
                                   and focus.get("role") == "AXLayoutArea"
                                   and focus.get("title") == "Character Animator")
                    windows = state.get("windows", [])
                    modal = (focus.get("modal") is True
                             or any(window.get("modal") is True or window.get("role") == "AXSheet"
                                    for window in windows if isinstance(window, dict)))
                    if (not main_window or modal):
                        raise CharacterRejected("Continue requires the exact Export Scene chooser or a background main window for guarded activation")
                    activated = self.bridge.call("focus", {"kind": "export_png_wav", "project_root": source_root},
                                                 expected=state, cancelled=cancelled)
                    activated_state = activated.get("state", {})
                    if (project_identity(activated_state) != packet["before_project_id"]
                            or activated_state.get("project_roots") != [source_root]
                            or (activated_state.get("pid"), activated_state.get("launch_time"))
                            != (state.get("pid"), state.get("launch_time"))
                            or activated_state.get("frontmost") is not True
                            or activated_state.get("authentication_or_permission_prompt") is True
                            or not self._chooser_visible(activated_state, "export_png_wav", True)):
                        raise CharacterRejected("Activation did not expose the exact same-process Export Scene chooser")
                    observed, state = activated, activated_state
                    current_phase = export_phase(state)
                    if current_phase is None:
                        raise CharacterRejected("Activation did not expose a typed export chooser phase")
                arguments = {"kind": "export_png_wav", "directory": str(target),
                             "basename": intent.get("name"), "project_root": source_root}

                process = (original.get("pid"), original.get("launch_time"))

                def settle_export_phase(expected_phase):
                    nonlocal observed, state
                    deadline = time.monotonic() + 3
                    previous_fingerprint = None
                    while time.monotonic() < deadline:
                        current = self.bridge.call("inspect", cancelled=cancelled)
                        current_state = current.get("state", {})
                        if ((current_state.get("pid"), current_state.get("launch_time")) != process
                                or project_identity(current_state) != packet["before_project_id"]
                                or current_state.get("project_roots") != [source_root]
                                or current_state.get("authentication_or_permission_prompt") is True):
                            raise CharacterRejected("The export process, project or prompt changed while its chooser settled")
                        if current_state.get("frontmost") is not True:
                            raise CharacterRejected("The exact export chooser is not in the foreground")
                        focused = current_state.get("focused_window") or {}
                        panel = (focused.get("identifier") == "save-panel"
                                 and focused.get("title") == "Export Scene")
                        go_to = (focused.get("identifier") == "GoToWindow"
                                 and focused.get("role") == "AXSheet"
                                 and self._chooser_visible(current_state, "export_png_wav", True))
                        phase = "export_panel" if panel else "go_to" if go_to else None
                        if phase == expected_phase:
                            fingerprint_value = current_state.get("fingerprint")
                            if (isinstance(fingerprint_value, str) and fingerprint_value
                                    and fingerprint_value == previous_fingerprint):
                                observed, state = current, current_state
                                return
                            previous_fingerprint = fingerprint_value
                        else:
                            previous_fingerprint = None
                            if phase is None:
                                modal = (focused.get("modal") is True or focused.get("role") == "AXSheet"
                                         or any(window.get("modal") is True or window.get("role") == "AXSheet"
                                                for window in current_state.get("windows", [])
                                                if isinstance(window, dict)))
                                if modal:
                                    raise CharacterRejected("An unrelated modal replaced the exact export chooser")
                        time.sleep(.1)
                    raise CharacterUnknown("The exact recorded export chooser phase did not reach a stable observation")

                def continue_export_step(expected_phase, command="chooser_continue_directory"):
                    nonlocal observed, state
                    settle_export_phase(expected_phase)
                    current = self.bridge.call(command, arguments, expected=state, cancelled=cancelled)
                    observed, state = current, current["state"]
                    packet.update(state="unknown", native=current, last_command=command)
                    write_json(self.directory / "operation.json", packet)
                    return current

                try:
                    if resume_phase == "initial_menu":
                        if current_phase == "export_panel":
                            selected = continue_export_step("export_panel", "chooser_destination")
                            status = selected.get("result", {}).get("status")
                            if status == "native_chooser_directory_requested":
                                selected = continue_export_step("go_to")
                            elif status != "native_chooser_submitted":
                                raise CharacterUnknown("Native export folder chooser did not open; inspect its pending operation")
                        else:
                            selected = continue_export_step("go_to")
                        if selected.get("result", {}).get("status") == "native_directory_requested":
                            selected = continue_export_step("export_panel")
                    elif resume_phase == "go_to":
                        selected = continue_export_step("go_to")
                        if selected.get("result", {}).get("status") == "native_directory_requested":
                            selected = continue_export_step("export_panel")
                    else:
                        selected = continue_export_step("export_panel")
                    if selected.get("result", {}).get("status") != "native_chooser_submitted":
                        raise CharacterUnknown("Native export chooser was not submitted; inspect its pending operation")
                    after = self.bridge.call("inspect", cancelled=cancelled)
                    if (project_identity(after["state"]) != packet["before_project_id"]
                            or after["state"].get("project_roots") != [source_root]
                            or (after["state"].get("pid"), after["state"].get("launch_time")) != process
                            or after["state"].get("authentication_or_permission_prompt") is True):
                        raise CharacterUnknown("Export submission changed the owned project or exposed a prompt")
                    result = {"status": "native_export_submitted", "completion": "unverified",
                              "directory": str(target), "name": intent["name"],
                              "expected_output_directory": str(target / intent["name"]),
                              "expected_xml": str(target / (intent["name"] + ".xml")),
                              "native_submission": selected, "native": after,
                              "output_verification": "use export_inspect after PNG/WAV files appear"}
                    packet.update(state="resolved", resolution="continue_export", result=result,
                                  native=after)
                    receipts = self.directory / "operations"
                    receipts.mkdir(exist_ok=True, mode=0o700)
                    write_json(receipts / (operation_id + ".json"), packet)
                    write_json(self.directory / "lease.json", lease)
                    (self.directory / "operation.json").unlink()
                    return {**result, "operation_id": operation_id, "retry_safe": False}
                except BaseException as exc:
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    packet.update(state="unknown", last_failure={
                        "code": getattr(exc, "error_code", "OUTCOME_UNKNOWN"),
                        "message": str(exc)[:4000]})
                    write_json(self.directory / "operation.json", packet)
                    if isinstance(exc, (CharacterRejected, CharacterUnknown)):
                        raise
                    raise CharacterUnknown("Export continuation remains unresolved; inspect its operation journal") from exc
            if action in ("continue_import", "confirm_cancelled_import"):
                intent = packet.get("intent", {})
                kind = intent.get("kind")
                extension = {"puppet_psd": ".psd", "audio_wav": ".wav"}.get(kind)
                if extension is None:
                    raise CharacterRejected("Pending import has no supported source kind")
                source = self.path(intent.get("source"))
                staged = Path(intent.get("import_copy", ""))
                input_root = (self.directory / "inputs").resolve(strict=True)
                if (source.suffix.lower() != extension or source.is_symlink() or not source.is_file()
                        or staged.is_symlink() or not staged.is_file()
                        or staged.resolve(strict=True).parent.parent != input_root
                        or not IDENTIFIER.fullmatch(staged.parent.name)
                        or (intent.get("import_directory_id") is not None
                            and staged.parent.name != intent.get("import_directory_id"))
                        or staged.name != source.name or staged.suffix.lower() != extension):
                    raise CharacterRejected("Pending import files do not match the retained private source lineage")
                source_before, staged_before = source.stat(), staged.stat()
                if ((intent.get("source_bytes") is not None
                     and source_before.st_size != intent.get("source_bytes"))
                        or (intent.get("import_copy_bytes") is not None
                            and staged_before.st_size != intent.get("import_copy_bytes"))
                        or source_before.st_size != staged_before.st_size):
                    raise CharacterRejected("Pending import file size differs from its journal")

                def hash_file(path):
                    before = path.stat()
                    with path.open("rb") as stream:
                        value = hashlib.file_digest(stream, "sha256").hexdigest()
                    after = path.stat()
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                        raise CharacterRejected("Pending import file changed while being verified")
                    return value

                if (hash_file(source) != intent.get("source_sha256")
                        or hash_file(staged) != intent.get("import_copy_sha256")
                        or intent.get("source_sha256") != intent.get("import_copy_sha256")):
                    raise CharacterRejected("Pending import hashes differ from the retained source identity")
                source_root = packet["parameters"].get("project_root")
                if (project_identity(state) != packet["before_project_id"]
                        or state.get("project_roots") != [source_root]
                        or lease.get("owned_root") != source_root
                        or state.get("authentication_or_permission_prompt") is True):
                    raise CharacterRejected("Continue requires the same owned project and process without a prompt")
                focus = state.get("focused_window") or {}
                if action == "confirm_cancelled_import":
                    modal_windows = [window for window in state.get("windows", []) if window.get("modal") is True]
                    chooser_focused = (focus.get("identifier") in ("open-panel", "GoToWindow")
                                       or focus.get("title") == "Import")
                    chooser_visible = any(window.get("identifier") in ("open-panel", "GoToWindow")
                                          or window.get("title") == "Import"
                                          for window in state.get("windows", []))
                    if modal_windows or chooser_focused or chooser_visible:
                        raise CharacterRejected("Cancellation confirmation requires no visible modal, Import or Go To chooser")
                    if (hash_file(source) != intent["source_sha256"]
                            or hash_file(staged) != intent["import_copy_sha256"]):
                        raise CharacterRejected("Pending import files changed before cancellation reconciliation")
                    result = {"status": "cancelled_user_reported",
                              "completion": "user_reported_native_cancellation_unverified",
                              "kind": kind,
                              "source": {"path": str(source), "sha256": intent["source_sha256"],
                                         "bytes": source_before.st_size},
                              "import_copy": {"path": str(staged), "sha256": intent["import_copy_sha256"],
                                              "bytes": staged_before.st_size},
                              "native": observed, "source_identity": "hashed_private_import_copy"}
                    packet.update(state="resolved", resolution="user_reported_cancellation",
                                  user_confirmed_cancelled=True, cancellation_native=observed,
                                  result=result)
                    receipts = self.directory / "operations"
                    receipts.mkdir(exist_ok=True, mode=0o700)
                    write_json(receipts / (operation_id + ".json"), packet)
                    write_json(self.directory / "lease.json", lease)
                    (self.directory / "operation.json").unlink()
                    return {**result, "operation_id": operation_id, "retry_safe": False}
                failure = packet.get("failure", {})
                recorded_pre_menu_failure = (
                    action == "continue_import"
                    and packet.get("native") is None
                    and packet.get("last_command") is None
                    and failure.get("code") == "OUTCOME_UNKNOWN"
                    and failure.get("message") == (
                        "[focus_dispatch] Character Animator did not become foreground through Accessibility"))
                if recorded_pre_menu_failure:
                    modal_windows = [window for window in state.get("windows", []) if window.get("modal") is True]
                    chooser_focused = (focus.get("identifier") in ("open-panel", "GoToWindow")
                                       or focus.get("title") == "Import")
                    chooser_visible = any(window.get("identifier") in ("open-panel", "GoToWindow")
                                          or window.get("title") == "Import"
                                          for window in state.get("windows", []))
                    if modal_windows or chooser_focused or chooser_visible:
                        raise CharacterRejected("Pre-menu failure reconciliation requires no visible modal or import chooser")
                    result = {"status": "rejected_before_menu_dispatch",
                              "basis": "recorded_native_pre_menu_failure",
                              "completion": "native_menu_dispatch_rejected",
                              "native": observed}
                    packet.update(state="resolved", resolution="rejected_before_menu_dispatch",
                                  reconciliation_basis="recorded_native_pre_menu_failure",
                                  reconciliation_native=observed, result=result)
                    receipts = self.directory / "operations"
                    receipts.mkdir(exist_ok=True, mode=0o700)
                    write_json(receipts / (operation_id + ".json"), packet)
                    write_json(self.directory / "lease.json", lease)
                    (self.directory / "operation.json").unlink()
                    return {**result, "operation_id": operation_id, "retry_safe": False}
                if not self._chooser_visible(state, "import", allow_go_to=True):
                    main_window = (state.get("frontmost") is False
                                   and focus.get("role") == "AXLayoutArea"
                                   and focus.get("title") == "Character Animator")
                    modal = (focus.get("modal") is True
                             or any(window.get("modal") is True or window.get("role") == "AXSheet"
                                    for window in state.get("windows", []) if isinstance(window, dict)))
                    if not main_window or modal or state.get("authentication_or_permission_prompt") is True:
                        raise CharacterRejected("Continue requires the exact existing Import or Go To chooser, or a background main window for guarded activation")
                    activated = self.bridge.call("focus", {"kind": "import", "project_root": source_root},
                                                 expected=state, cancelled=cancelled)
                    activated_state = activated.get("state", {})
                    if (project_identity(activated_state) != packet["before_project_id"]
                            or activated_state.get("project_roots") != [source_root]
                            or (activated_state.get("pid"), activated_state.get("launch_time"))
                            != (state.get("pid"), state.get("launch_time"))
                            or activated_state.get("frontmost") is not True
                            or activated_state.get("authentication_or_permission_prompt") is True
                            or not self._chooser_visible(activated_state, "import", allow_go_to=True)):
                        raise CharacterRejected("Activation did not expose the exact same-process Import chooser")
                    observed, state, focus = activated, activated_state, activated_state.get("focused_window") or {}
                if not ((focus.get("identifier") == "open-panel" and focus.get("title") == "Import")
                        or (focus.get("identifier") == "GoToWindow" and focus.get("role") == "AXSheet")):
                    raise CharacterRejected("Continue requires the exact existing Import or Go To chooser")
                arguments = {"kind": "import", "directory": str(staged.parent),
                             "basename": staged.name, "project_root": source_root}

                def dispatch(command, _arguments):
                    nonlocal observed, state
                    previous = state.get("fingerprint")
                    deadline = time.monotonic() + 3
                    stable = False
                    while time.monotonic() < deadline:
                        time.sleep(.1)
                        current = self.bridge.call("inspect", cancelled=cancelled)
                        current_state = current["state"]
                        if (project_identity(current_state) != packet["before_project_id"]
                                or current_state.get("project_roots") != [source_root]
                                or current_state.get("authentication_or_permission_prompt") is True):
                            raise CharacterRejected("Project, process or prompt changed while the import chooser settled")
                        current_focus = current_state.get("focused_window") or {}
                        valid_focus = ((current_focus.get("identifier") == "open-panel"
                                        and current_focus.get("title") == "Import")
                                       or (current_focus.get("identifier") == "GoToWindow"
                                           and current_focus.get("role") == "AXSheet"))
                        if not valid_focus:
                            raise CharacterRejected("The exact existing Import or Go To chooser is no longer focused")
                        fingerprint = current_state.get("fingerprint")
                        if fingerprint and fingerprint == previous:
                            observed, state, stable = current, current_state, True
                            break
                        previous = fingerprint
                        observed, state = current, current_state
                    if not stable:
                        raise CharacterUnknown("The exact import chooser did not reach a stable observed state")
                    observed = self.bridge.call(command, _arguments, expected=state, cancelled=cancelled)
                    state = observed["state"]
                    packet.update(state="unknown", native=observed, last_command=command)
                    write_json(self.directory / "operation.json", packet)
                    return observed

                try:
                    start_at = "go_to_folder" if focus.get("identifier") == "GoToWindow" else "import_panel"
                    self._select_import_file(dispatch, arguments, start_at)
                    after = self.bridge.call("inspect", cancelled=cancelled)
                    if (project_identity(after["state"]) != packet["before_project_id"]
                            or after["state"].get("project_roots") != [source_root]
                            or after["state"].get("authentication_or_permission_prompt") is True
                            or hash_file(source) != intent["source_sha256"]
                            or hash_file(staged) != intent["import_copy_sha256"]):
                        raise CharacterUnknown("Import selection changed project, prompt or source evidence")
                    result = {"status": "native_import_submitted", "completion": "unverified",
                              "kind": kind, "source": {"path": str(source),
                                  "sha256": intent["source_sha256"], "bytes": source_before.st_size},
                              "import_copy": {"path": str(staged),
                                  "sha256": intent["import_copy_sha256"], "bytes": staged_before.st_size},
                              "native": after, "source_identity": "hashed_private_import_copy"}
                    packet.update(state="resolved", resolution="continue_import", result=result,
                                  native=after)
                    receipts = self.directory / "operations"
                    receipts.mkdir(exist_ok=True, mode=0o700)
                    write_json(receipts / (operation_id + ".json"), packet)
                    write_json(self.directory / "lease.json", lease)
                    (self.directory / "operation.json").unlink()
                    return {**result, "operation_id": operation_id, "retry_safe": False}
                except BaseException as exc:
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    packet.update(state="unknown", last_failure={
                        "code": getattr(exc, "error_code", "OUTCOME_UNKNOWN"),
                        "message": str(exc)[:4000]})
                    write_json(self.directory / "operation.json", packet)
                    if isinstance(exc, (CharacterRejected, CharacterUnknown)):
                        raise
                    raise CharacterUnknown("Import continuation remains unresolved; inspect its operation journal") from exc
            if packet["operation"] == "save_as":
                destination = self.path(packet["intent"]["destination"])
            else:
                destination = self.path(packet["intent"]["directory"])
            if action == "continue_copy":
                source, _, destination = self._initial_copy_paths(packet)
                source_root = str(source)
                focus = state.get("focused_window") or {}
                if (project_identity(state) != packet["before_project_id"]
                        or state.get("project_roots") != [source_root]
                        or state.get("authentication_or_permission_prompt") is True
                        or destination.exists()):
                    raise CharacterRejected("Continue requires the original project and its exact unsubmitted Save As chooser")
                is_save_panel = (focus.get("identifier") == "save-panel"
                                 and focus.get("title") == "Save Project As")
                is_go_to = (focus.get("identifier") == "GoToWindow" and focus.get("role") == "AXSheet")
                if not (is_save_panel or is_go_to):
                    main_window = (focus.get("role") == "AXLayoutArea"
                                   and focus.get("title") == "Character Animator")
                    modal = (focus.get("modal") is True
                             or any(window.get("modal") is True or window.get("role") == "AXSheet"
                                    for window in state.get("windows", []) if isinstance(window, dict)))
                    if (state.get("frontmost") is not False or not main_window or modal):
                        raise CharacterRejected("Continue requires the exact existing Save As chooser or a background main window for guarded activation")
                    focused = self.bridge.call("focus", {"kind": "save_as", "project_root": source_root},
                                               expected=state, cancelled=cancelled)
                    focused_state = focused.get("state", {})
                    after_focus = focused_state.get("focused_window") or {}
                    focused_panel = (after_focus.get("identifier") == "save-panel"
                                     and after_focus.get("title") == "Save Project As")
                    focused_go_to = (after_focus.get("identifier") == "GoToWindow"
                                     and after_focus.get("role") == "AXSheet"
                                     and any(window.get("identifier") == "save-panel"
                                             and window.get("title") == "Save Project As"
                                             for window in focused_state.get("windows", [])
                                             if isinstance(window, dict)))
                    if (project_identity(focused_state) != packet["before_project_id"]
                            or focused_state.get("project_roots") != [source_root]
                            or focused_state.get("authentication_or_permission_prompt") is True
                            or focused_state.get("frontmost") is not True
                            or (focused_state.get("pid"), focused_state.get("launch_time"))
                            != (state.get("pid"), state.get("launch_time"))
                            or not (focused_panel or focused_go_to)):
                        raise CharacterRejected("Activation did not expose the exact same-process Save As chooser")
                    observed, state, focus = focused, focused_state, after_focus
                arguments = {"kind": "save_as", "directory": packet["intent"]["directory"],
                             "basename": packet["intent"]["name"], "project_root": source_root}

                def dispatch(command, parameters):
                    nonlocal observed, state
                    previous = state.get("fingerprint")
                    deadline = time.monotonic() + 3
                    stable = False
                    while time.monotonic() < deadline:
                        time.sleep(.1)
                        current = self.bridge.call("inspect", cancelled=cancelled)
                        current_state = current["state"]
                        if (project_identity(current_state) != packet["before_project_id"]
                                or current_state.get("project_roots") != [source_root]
                                or current_state.get("authentication_or_permission_prompt") is True):
                            raise CharacterRejected("Project, process or prompt changed while the native chooser settled")
                        if not self._chooser_visible(current_state, "save_as",
                                                     allow_go_to=command == "chooser_continue_directory"):
                            raise CharacterRejected("The exact owned Save As chooser is no longer focused")
                        fingerprint = current_state.get("fingerprint")
                        if fingerprint and fingerprint == previous:
                            observed, state, stable = current, current_state, True
                            break
                        previous = fingerprint
                        observed, state = current, current_state
                    if not stable:
                        raise CharacterUnknown("The exact native chooser did not reach a stable observed state")
                    observed = self.bridge.call(command, parameters, expected=state, cancelled=cancelled)
                    state = observed["state"]
                    packet.update(state="unknown", native=observed, last_command=command)
                    write_json(self.directory / "operation.json", packet)
                    return observed

                try:
                    if focus.get("identifier") == "GoToWindow":
                        result = dispatch("chooser_continue_directory", arguments)
                    else:
                        result = dispatch("chooser_destination", arguments)
                    if (focus.get("identifier") == "save-panel"
                            and result["result"].get("status") != "native_chooser_submitted"):
                        result = dispatch("chooser_continue_directory", arguments)
                    for _ in range(2):
                        if result["result"].get("status") != "native_directory_requested":
                            break
                        result = dispatch("chooser_continue_directory", arguments)
                    deadline = time.monotonic() + 15
                    while str(destination) not in state.get("project_roots", []):
                        if state.get("authentication_or_permission_prompt") is True:
                            raise CharacterUnknown("A native prompt interrupted Save As; inspect and resolve it manually")
                        if cancelled is not None and cancelled.is_set():
                            raise CharacterUnknown("Copy continuation interrupted; inspect its destination")
                        if time.monotonic() >= deadline:
                            raise CharacterUnknown("Continued Save As copy is not the observed native project")
                        time.sleep(.1)
                        observed = self.bridge.call("inspect", cancelled=cancelled)
                        state = observed["state"]
                    files = list(destination.glob("*.chproj"))
                    if len(files) != 1 or not (destination / "Ch Data").is_dir():
                        raise CharacterUnknown("Continued native copy lacks its project file or project data")
                    result = {"status": "owned_copy", "project_id": project_identity(state),
                              "project_root": str(destination), "project_file": str(files[0]),
                              "files": self.project_files(destination), "original_source_hash": None,
                              "source_identity": "observed_native_project_root", "native": observed}
                    lease.update(project_id=result["project_id"], owned_root=str(destination))
                    packet.update(state="resolved", resolution="continue_copy", result=result)
                    receipts = self.directory / "operations"
                    receipts.mkdir(exist_ok=True, mode=0o700)
                    write_json(receipts / (operation_id + ".json"), packet)
                    write_json(self.directory / "lease.json", lease)
                    (self.directory / "operation.json").unlink()
                    return {**result, "operation_id": operation_id, "retry_safe": False}
                except BaseException as exc:
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    packet.update(state="unknown", last_failure={
                        "code": getattr(exc, "error_code", "OUTCOME_UNKNOWN"),
                        "message": str(exc)[:4000]})
                    write_json(self.directory / "operation.json", packet)
                    if isinstance(exc, CharacterUnknown):
                        raise
                    raise CharacterUnknown("Save As continuation remains unresolved; inspect its operation journal") from exc
            if action == "verify_copy":
                if state.get("project_roots") != [str(destination)]:
                    raise CharacterRejected("Native project root does not identify the expected copy")
                files = list(destination.glob("*.chproj"))
                if len(files) != 1 or not (destination / "Ch Data").is_dir():
                    raise CharacterRejected("Expected native project files are incomplete")
                result = {"status": "owned_copy", "project_root": str(destination),
                          "project_file": str(files[0]), "files": self.project_files(destination),
                          "source_identity": "observed_native_project_root", "original_source_hash": None,
                          "project_id": project_identity(state), "native": observed}
                lease.update(project_id=result["project_id"], owned_root=str(destination))
            else:
                source_root = packet["parameters"]["project_root"]
                modal = state.get("focused_window") or {}
                export_cancel = packet["operation"] == "export_png_wav"
                name = packet.get("intent", {}).get("name")
                if export_cancel:
                    expected_identity = packet.get("intent", {}).get("output_directory_identity")
                    target_stat = destination.stat() if destination.is_dir() and not destination.is_symlink() else None
                    export_parent = any(window.get("identifier") == "save-panel"
                                        and window.get("title") == "Export Scene"
                                        for window in state.get("windows", []))
                    if (target_stat is None or not isinstance(name, str)
                            or (expected_identity is not None
                                and expected_identity != {"device": target_stat.st_dev, "inode": target_stat.st_ino})
                            or (destination / name).exists() or (destination / (name + ".xml")).exists()
                            or any(destination.iterdir())
                            or (modal.get("identifier") == "GoToWindow" and not export_parent)):
                        raise CharacterRejected("Only the unsubmitted owned Export Scene chooser with an empty output directory can be cancelled")
                if modal.get("identifier") == "GoToWindow":
                    self.bridge.call("chooser_cancel", {"kind": "go_to_folder"}, expected=state,
                                     cancelled=cancelled)
                    observed = self.bridge.call("inspect", cancelled=cancelled)
                    state = observed["state"]
                    modal = state.get("focused_window") or {}
                expected_title = "Export Scene" if export_cancel else "Save Project As"
                if ((not export_cancel and destination.exists())
                        or state.get("project_roots") != [source_root]
                        or modal.get("identifier") != "save-panel" or modal.get("title") != expected_title
                        or state.get("authentication_or_permission_prompt") is True):
                    raise CharacterRejected("Only the unsubmitted owned save/export chooser with no expected output can be cancelled")
                try:
                    chooser_kind = "export_png_wav" if export_cancel else "save_as"
                    self.bridge.call("chooser_cancel", {"kind": chooser_kind}, expected=state, cancelled=cancelled)
                    deadline = time.monotonic() + 3
                    while True:
                        after = self.bridge.call("inspect", cancelled=cancelled)
                        if (after["state"].get("project_roots") != [source_root]
                                or (not export_cancel and destination.exists())
                                or (export_cancel and ((destination / name).exists()
                                                       or (destination / (name + ".xml")).exists()
                                                       or any(destination.iterdir())))):
                            raise CharacterUnknown("Native project or output changed during cancellation")
                        if not any(window.get("modal") is True for window in after["state"].get("windows", [])):
                            break
                        if time.monotonic() >= deadline:
                            raise CharacterUnknown("Native cancellation did not reach the expected project state")
                        time.sleep(.1)
                    result = {"status": "cancelled", "completion":
                              "unsubmitted_export_cancelled" if export_cancel else "unsubmitted_copy_cancelled",
                              "native": after, "expected_output_exists": False}
                except BaseException as exc:
                    packet.update(state="unknown", failure={"code": getattr(exc, "error_code", "OUTCOME_UNKNOWN"), "message": str(exc)[:4000]})
                    write_json(self.directory / "operation.json", packet)
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    raise CharacterUnknown("Cancellation was not verified; the operation remains unresolved") from exc
            packet.update(state="resolved", resolution=action, result=result)
            receipts = self.directory / "operations"
            receipts.mkdir(exist_ok=True, mode=0o700)
            try:
                write_json(receipts / (operation_id + ".json"), packet)
                write_json(self.directory / "lease.json", lease)
                (self.directory / "operation.json").unlink()
            except OSError as exc:
                raise CharacterUnknown("Native outcome was observed but its resolution journal was not committed") from exc
            return {**result, "operation_id": operation_id, "retry_safe": False}
