"""Ownership/error/protocol fixtures; these do not establish native CA behavior."""

import copy
import ctypes
import errno
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cavi_mcp_core.contracts import failure
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from cavi_adobe_mcp.character_animator import CharacterAnimator, write_json
from cavi_adobe_mcp.character_bridge import CharacterRejected, CharacterUnknown

ROOT = Path(__file__).resolve().parents[1]


class NativeFixture:
    def __init__(self, root):
        root = root.resolve()
        self.state = {"bundle_id": "fixture", "pid": 42, "launch_time": "fixture",
                      "frontmost": True,
                      "project_roots": [str(root / "original")], "fingerprint": "initial"}
        self.state["windows"] = []
        self.calls = []
        self.fail = False
        self.directory_transition = False
        self.fail_submit = False
        self.import_directory_pending = False
        self.fail_import_before_envelope = False
        self.fail_import_focus_dispatch = False
        self.fail_import_generic_no_menu = False
        self.fail_export_submit = False
        self.fail_export_destination = False
        self.hide_chooser_after_command = False
        self.export_inspect_states = []
        self.open_without_root = False

    def call(self, operation, parameters=None, expected=None, cancelled=None):
        self.calls.append(operation)
        if expected is not None and expected["fingerprint"] != self.state["fingerprint"]:
            raise CharacterRejected("Stale observation", "STALE_STATE")
        if (operation == "chooser_destination"
                and (parameters or {}).get("kind") == "export_png_wav"
                and self.fail_export_destination):
            raise CharacterRejected("Stale export chooser observation", "STALE_STATE")
        if operation == "inspect" and self.export_inspect_states:
            self.state.update(copy.deepcopy(self.export_inspect_states.pop(0)))
        if operation == "focus":
            kind = (parameters or {}).get("kind")
            panel_id, title = (("open-panel", "Import") if kind == "import" else
                               ("save-panel", "Export Scene") if kind == "export_png_wav" else
                               ("open-panel", "Open Project") if kind == "open_project" else
                               ("save-panel", "Save Project As"))
            self.state.update(frontmost=True, fingerprint="focused-save-as",
                              focused_window={"identifier": panel_id, "title": title, "modal": True},
                              windows=[{"identifier": panel_id, "title": title, "modal": True}])
            return {"version": 1, "ok": True, "operation": operation,
                    "state": copy.deepcopy(self.state),
                    "result": {"status": "native_application_focused", "completion": "observed", "ui_only": True}}
        if operation == "open_prepared_copy":
            root = (parameters or {})["project_root"]
            self.state.update(bundle_id="com.adobe.Character-Animator.application",
                              application="/Applications/Adobe Character Animator 2026/Adobe Character Animator 2026.app",
                              pid=99, launch_time=99.0, running=True, process_count=1,
                              authentication_or_permission_prompt=False,
                              project_roots=[] if self.open_without_root else [root],
                              fingerprint="prepared-open")
            return {"version": 1, "ok": True, "operation": operation,
                    "state": copy.deepcopy(self.state),
                    "result": {"status": "native_prepared_copy_open_requested",
                               "completion": "unverified", "process_id": 99, "launch_time": 99.0}}
        if operation == "save_as":
            self.state["fingerprint"] = "chooser"
            self.state["focused_window"] = {"identifier": "save-panel",
                                             "title": "Save Project As"}
            self.state["windows"] = [{"modal": True, "identifier": "save-panel",
                                       "title": "Save Project As"}]
            if self.hide_chooser_after_command:
                self.state.update(frontmost=False, fingerprint="background-main",
                                  focused_window={"identifier": "main-window", "role": "AXLayoutArea",
                                                  "title": "Character Animator"},
                                  windows=[{"identifier": "main-window", "role": "AXWindow",
                                            "title": "Character Animator", "modal": False}])
        if operation == "chooser_destination":
            self.state["fingerprint"] = "go-to"
            self.state["focused_window"] = {"identifier": "GoToWindow", "role": "AXSheet"}
            kind = (parameters or {}).get("kind")
            panel_id, title = (("open-panel", "Import") if kind == "import" else
                               ("save-panel", "Export Scene") if kind == "export_png_wav" else
                               ("open-panel", "Open Project") if kind == "open_project" else
                               ("save-panel", "Save Project As"))
            self.state["windows"] = [{"identifier": panel_id, "title": title, "modal": True},
                                      {"identifier": "GoToWindow", "role": "AXSheet"}]
        if operation == "chooser_continue_directory":
            if self.fail:
                raise CharacterUnknown("Fixture timeout")
            if parameters.get("kind") == "export_png_wav":
                if self.state.get("focused_window", {}).get("identifier") == "GoToWindow":
                    self.state["fingerprint"] = "export-directory-selected"
                    self.state["focused_window"] = {"identifier": "save-panel", "title": "Export Scene",
                                                    "modal": True}
                    self.state["windows"] = [{"identifier": "save-panel", "title": "Export Scene",
                                               "modal": True}]
                    return {"version": 1, "ok": True, "operation": operation,
                            "state": copy.deepcopy(self.state),
                            "result": {"status": "native_directory_requested"}}
                if self.fail_export_submit:
                    raise CharacterUnknown("Fixture export chooser timeout")
                self.state["fingerprint"] = "export-submitted"
                self.state["focused_window"] = {"identifier": "main-window", "title": "Character Animator"}
                self.state["windows"] = []
                return {"version": 1, "ok": True, "operation": operation,
                        "state": copy.deepcopy(self.state),
                        "result": {"status": "native_chooser_submitted", "completion": "unverified"}}
            if parameters.get("kind") == "import":
                if self.import_directory_pending:
                    return {"version": 1, "ok": True, "operation": operation,
                            "state": copy.deepcopy(self.state),
                            "result": {"status": "native_directory_requested"}}
                self.state["fingerprint"] = "import-submitted"
                self.state["focused_window"] = None
                self.state["windows"] = []
                return {"version": 1, "ok": True, "operation": operation,
                        "state": copy.deepcopy(self.state),
                        "result": {"status": "native_chooser_submitted", "completion": "unverified"}}
            if self.directory_transition and self.state["focused_window"]["identifier"] == "GoToWindow":
                self.state["fingerprint"] = "directory-selected"
                self.state["focused_window"] = {"identifier": "save-panel", "title": "Save Project As"}
                self.state["windows"] = [{"modal": True, "identifier": "save-panel", "title": "Save Project As"}]
                return {"version": 1, "ok": True, "operation": operation,
                        "state": copy.deepcopy(self.state), "result": {"status": "native_directory_requested"}}
            if self.fail_submit:
                raise CharacterUnknown("Fixture Save failure")
            destination = Path(parameters["directory"]) / parameters["basename"]
            destination.mkdir()
            (destination / (parameters["basename"] + ".chproj")).write_bytes(b"")
            (destination / "Ch Data").mkdir()
            (destination / "Ch Data/projectdata.rope").write_bytes(b"fixture native project")
            self.state["project_roots"] = [str(destination)]
            self.state["fingerprint"] = "copied"
            self.state["focused_window"] = {"identifier": "main-window", "title": "Character Animator"}
            self.state["windows"] = []
        if operation == "chooser_cancel":
            if parameters.get("kind") == "go_to_folder":
                self.state["fingerprint"] = "save-panel-after-go-to"
                self.state["focused_window"] = {"identifier": "save-panel", "title": "Save Project As"}
                self.state["windows"] = [{"modal": True, "identifier": "save-panel",
                                           "title": "Save Project As"}]
            else:
                self.state["fingerprint"] = "cancelled"
                self.state["focused_window"] = None
                self.state["windows"] = []
        if operation == "import":
            if self.fail_import_focus_dispatch or self.fail_import_generic_no_menu:
                self.state["fingerprint"] = "main-window"
                self.state["focused_window"] = {"identifier": "main-window", "title": "Character Animator"}
                self.state["windows"] = []
                message = ("[focus_dispatch] Character Animator did not become foreground through Accessibility"
                           if self.fail_import_focus_dispatch else "Fixture generic unknown before menu")
                raise CharacterUnknown(message)
            self.state["fingerprint"] = "import-panel"
            self.state["focused_window"] = {"identifier": "open-panel", "title": "Import", "modal": True}
            self.state["windows"] = [{"modal": True, "identifier": "open-panel", "title": "Import"}]
            if self.hide_chooser_after_command:
                self.state.update(frontmost=False, fingerprint="background-main",
                                  focused_window={"identifier": "main-window", "role": "AXLayoutArea",
                                                  "title": "Character Animator"},
                                  windows=[{"identifier": "main-window", "role": "AXWindow",
                                            "title": "Character Animator", "modal": False}])
            if self.fail_import_before_envelope:
                raise CharacterUnknown("Fixture timeout before native response envelope")
        if operation == "chooser_destination" and parameters.get("kind") == "import":
            if self.import_directory_pending:
                return {"version": 1, "ok": True, "operation": operation,
                        "state": copy.deepcopy(self.state),
                        "result": {"status": "native_chooser_directory_requested"}}
            self.state["fingerprint"] = "import-submitted"
            self.state["focused_window"] = None
            self.state["windows"] = []
            return {"version": 1, "ok": True, "operation": operation,
                    "state": copy.deepcopy(self.state),
                    "result": {"status": "native_chooser_submitted", "completion": "unverified"}}
        if operation == "chooser_destination" and parameters.get("kind") == "export_png_wav":
            return {"version": 1, "ok": True, "operation": operation,
                    "state": copy.deepcopy(self.state),
                    "result": {"status": "native_chooser_directory_requested", "completion": "unverified"}}
        if operation == "export_png_wav":
            self.state["fingerprint"] = "export-panel"
            self.state["focused_window"] = {"identifier": "save-panel", "title": "Export Scene", "modal": True}
            self.state["windows"] = [{"identifier": "save-panel", "title": "Export Scene", "modal": True}]
            if self.hide_chooser_after_command:
                self.state.update(frontmost=False, fingerprint="background-main",
                                  focused_window={"identifier": "main-window", "role": "AXLayoutArea",
                                                  "title": "Character Animator"},
                                  windows=[{"identifier": "main-window", "role": "AXWindow",
                                            "title": "Character Animator", "modal": False}])
            return {"version": 1, "ok": True, "operation": operation,
                    "state": copy.deepcopy(self.state),
                    "result": {"status": "native_command_dispatched", "completion": "unverified"}}
        if operation == "project_select":
            return {"version": 1, "ok": True, "operation": operation,
                    "state": copy.deepcopy(self.state),
                    "result": {"status": "native_project_item_selected",
                               "selection_evidence": {"ocr_identity": "fixture", "selected": True}}}
        return {"version": 1, "ok": True, "operation": operation,
                "state": copy.deepcopy(self.state), "result": {"status": "fixture"}}


# Exclusive project publication uses macOS filesystem calls.
MACOS_ONLY = unittest.skipUnless(sys.platform == "darwin", "needs macOS")


class CharacterOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        (self.root / "workspace").mkdir()
        (self.root / "workspace/copies").mkdir()
        (self.root / "original").mkdir()
        self.native = NativeFixture(self.root)
        self.client = CharacterAnimator(self.root / "workspace", self.root / "private", self.native)

    def claim(self):
        observed = self.client.inspect()
        return self.client.session("claim", "fixture", observed["project_id"])

    @MACOS_ONLY
    def test_prepare_closed_copy_open_exact_root_and_claim_inode_ownership(self):
        source = self.closed_character_project()
        result = self.client.prepare_copy("ClosedProject", "copies/PreparedProject")
        destination = (self.root / "workspace/copies/PreparedProject").resolve()

        self.assertEqual(result["status"], "prepared_copy")
        self.assertFalse(result["launches_application"])
        self.assertEqual(result["destination_identity"], self.client._directory_identity(destination))
        self.assertEqual(result["files"], self.client._project_files_from_root(source))
        self.assertEqual(result["files"], self.client._project_files_from_root(destination))
        self.assertFalse(self.native.state["running"])

        opened = self.client.session("open_prepared_copy", prepared_copy_id=result["prepared_copy_id"])
        self.assertEqual(opened["status"], "prepared_copy_opened")
        self.assertEqual(opened["completion"], "observed")
        self.assertEqual(opened["native"]["state"]["project_roots"], [str(destination)])
        self.assertIsNone(self.client.read("operation.json"))

        observed = self.client.inspect()
        lease = self.client.session("claim", "fixture", observed["project_id"],
                                    prepared_copy_id=result["prepared_copy_id"])
        self.assertEqual(lease["owned_root"], str(destination))
        self.assertEqual(lease["prepared_copy_id"], result["prepared_copy_id"])
        stored_lease = self.client.read("lease.json")
        self.assertEqual(stored_lease["owned_root_identity"], self.client._directory_identity(destination))

    @MACOS_ONLY
    def test_claim_resolves_only_the_matching_unknown_prepared_open_without_replaying(self):
        self.closed_character_project()
        prepared = self.client.prepare_copy("ClosedProject", "copies/PreparedProject")
        self.native.open_without_root = True
        opened = self.client.session("open_prepared_copy", prepared_copy_id=prepared["prepared_copy_id"])
        self.assertEqual(opened["status"], "prepared_copy_open_requested")
        packet = self.client.read("operation.json")
        self.assertEqual(packet["operation"], "open_prepared_copy")
        self.assertEqual(packet["state"], "unknown")
        open_calls = self.native.calls.count("open_prepared_copy")

        with self.assertRaisesRegex(CharacterRejected, "no Character Animator instance or operation is active"):
            self.client.session("open_prepared_copy", prepared_copy_id=prepared["prepared_copy_id"])
        self.assertEqual(self.native.calls.count("open_prepared_copy"), open_calls)

        destination = (self.root / "workspace/copies/PreparedProject").resolve()
        self.native.state.update(project_roots=[str(destination)], fingerprint="prepared-root-loaded")
        observed = self.client.inspect()
        lease = self.client.session("claim", "fixture", observed["project_id"],
                                    prepared_copy_id=prepared["prepared_copy_id"])
        self.assertEqual(lease["owned_root"], str(destination))
        self.assertIsNone(self.client.read("operation.json"))
        archived = self.client.read("operations/" + packet["operation_id"] + ".json")
        self.assertEqual(archived["resolution"], "prepared_copy_open_verified")

    @MACOS_ONLY
    def test_prepared_copy_claim_rejects_changed_process_root_or_inode(self):
        source = self.closed_character_project()
        prepared = self.client.prepare_copy("ClosedProject", "copies/PreparedProject")
        destination = self.root / "workspace/copies/PreparedProject"
        lease_file = self.client.directory / "lease.json"
        original_state = copy.deepcopy(self.native.state)
        cases = (
            ("wrong root", lambda: self.native.state.update(
                bundle_id="com.adobe.Character-Animator.application", application=original_state["application"],
                running=True, process_count=1, pid=99, launch_time=99.0,
                project_roots=[str(self.root / "workspace/other")])),
            ("permission prompt", lambda: self.native.state.update(
                bundle_id="com.adobe.Character-Animator.application", application=original_state["application"],
                running=True, process_count=1, pid=99, launch_time=99.0,
                project_roots=[str(destination)], authentication_or_permission_prompt=True)),
        )
        for label, damage in cases:
            with self.subTest(case=label):
                self.native.state = copy.deepcopy(original_state)
                damage()
                observed = self.client.inspect()
                with self.assertRaises(CharacterRejected):
                    self.client.session("claim", "fixture", observed["project_id"],
                                        prepared_copy_id=prepared["prepared_copy_id"])
                self.assertFalse(lease_file.exists())

        self.native.state = copy.deepcopy(original_state)
        shutil.rmtree(destination)
        shutil.copytree(source, destination)
        self.native.state.update(bundle_id="com.adobe.Character-Animator.application",
                                 application=original_state["application"], running=True,
                                 process_count=1, pid=99, launch_time=99.0,
                                 project_roots=[str(destination)])
        observed = self.client.inspect()
        with self.assertRaisesRegex(CharacterRejected, "prepared copy or its recorded source lineage changed"):
            self.client.session("claim", "fixture", observed["project_id"],
                                prepared_copy_id=prepared["prepared_copy_id"])
        self.assertFalse(lease_file.exists())

    def test_prepare_closed_copy_rejects_open_symlinked_or_mutating_sources(self):
        source = self.closed_character_project()
        self.native.state["project_roots"] = [str(source)]
        with self.assertRaisesRegex(CharacterRejected, "source must be closed"):
            self.client.prepare_copy("ClosedProject", "copies/OpenSource")
        self.assertFalse((self.root / "workspace/copies/OpenSource").exists())

        self.native.state.update(running=False, process_count=0, pid=None, launch_time=None)
        self.native.state.pop("project_roots", None)
        outside = self.root / "outside.txt"
        outside.write_text("outside")
        (source / "Ch Data/external-link").symlink_to(outside)
        with self.assertRaises(CharacterRejected):
            self.client.prepare_copy("ClosedProject", "copies/SymlinkSource")
        self.assertFalse((self.root / "workspace/copies/SymlinkSource").exists())

    @MACOS_ONLY
    def test_prepare_closed_copy_rejects_source_changes_and_never_adopts_unreceipted_copy(self):
        source = self.closed_character_project()
        original_copytree = shutil.copytree

        def mutate_source(src, dst, *args, **kwargs):
            result = original_copytree(src, dst, *args, **kwargs)
            (source / "Ch Data/projectdata.rope").write_bytes(b"changed during copy")
            return result

        with (patch("cavi_adobe_mcp.character_animator.shutil.copytree", mutate_source),
              self.assertRaisesRegex(CharacterRejected, "Source changed while preparing")):
            self.client.prepare_copy("ClosedProject", "copies/ChangedSource")
        self.assertFalse((self.root / "workspace/copies/ChangedSource").exists())

        fixed_id = "2" * 32
        ids = [SimpleNamespace(hex="1" * 32), SimpleNamespace(hex=fixed_id)]
        original_write = write_json

        def fail_receipt(path, value):
            if Path(path).parent.name == "prepared-copies":
                raise OSError("fixture crash after publication")
            return original_write(path, value)

        with (patch("cavi_adobe_mcp.character_animator.uuid.uuid4", side_effect=ids),
              patch("cavi_adobe_mcp.character_animator.write_json", fail_receipt),
              self.assertRaisesRegex(CharacterUnknown, "may be incomplete")):
            self.client.prepare_copy("ClosedProject", "copies/UnreceiptedCopy")
        destination = self.root / "workspace/copies/UnreceiptedCopy"
        self.assertTrue(destination.is_dir())
        self.native.state.update(bundle_id="com.adobe.Character-Animator.application",
                                 application="/Applications/Adobe Character Animator 2026/Adobe Character Animator 2026.app",
                                 running=True, process_count=1, pid=99, launch_time=99.0,
                                 project_roots=[str(destination)])
        observed = self.client.inspect()
        with self.assertRaisesRegex(CharacterRejected, "no verified private copy receipt"):
            self.client.session("claim", "fixture", observed["project_id"], prepared_copy_id=fixed_id)

    @MACOS_ONLY
    def test_prepare_copy_preserves_destination_created_at_exclusive_publish(self):
        self.closed_character_project()
        destination = (self.root / "workspace/copies/RaceDestination").resolve()
        unrelated_inode = None

        def racing_rename(source, target, flags):
            nonlocal unrelated_inode
            self.assertEqual(flags, 0x00000004)
            Path(os.fsdecode(target)).mkdir()
            unrelated_inode = Path(os.fsdecode(target)).stat().st_ino
            ctypes.set_errno(errno.EEXIST)
            return -1

        with patch("cavi_adobe_mcp.character_animator.ctypes.CDLL") as cdll:
            cdll.return_value.renamex_np = racing_rename
            with self.assertRaisesRegex(CharacterRejected, "Output must not already exist"):
                self.client.prepare_copy("ClosedProject", "copies/RaceDestination")

        self.assertTrue(destination.is_dir())
        self.assertEqual(destination.stat().st_ino, unrelated_inode)
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual(list((self.root / "workspace/copies").glob(".RaceDestination.prepare-*")), [])
        self.assertEqual(list((self.client.directory / "prepared-copies").glob("*.json")), [])

    @MACOS_ONLY
    def test_prepare_copy_reports_post_publish_verification_failure_as_unknown(self):
        self.closed_character_project()
        destination = (self.root / "workspace/copies/PublishedCopy").resolve()
        original_directories = self.client._project_directories

        def inject_symlink_after_publish(root):
            if root == destination:
                (root / "Ch Data/unexpected-link").symlink_to(self.root / "outside.txt")
                raise CharacterRejected("Project tree changed during final verification")
            return original_directories(root)

        with (patch.object(self.client, "_project_directories", side_effect=inject_symlink_after_publish),
              self.assertRaisesRegex(CharacterUnknown, "was published but verification failed")):
            self.client.prepare_copy("ClosedProject", "copies/PublishedCopy")

        self.assertTrue(destination.is_dir())
        self.assertTrue((destination / "Ch Data/unexpected-link").is_symlink())
        self.assertFalse((self.client.directory / "prepared-copies").exists())

    def pending_save_as(self):
        lease = self.claim()
        self.native.fail = True
        with self.assertRaises(CharacterUnknown):
            self.client.save_copy("copies", "Recovery", lease["lease_id"])
        packet = self.client.inspect()["pending_operation"]
        return lease, packet

    def pending_closed_initial_save_as(self):
        source = self.root / "workspace/SignalSparkHomeport"
        source.mkdir()
        self.native.state["project_roots"] = [str(source.resolve())]
        lease, packet = self.pending_save_as()
        packet["last_command"] = "chooser_destination"
        packet["native"]["operation"] = "chooser_destination"
        packet["native"]["result"]["status"] = "native_chooser_directory_requested"
        write_json(self.client.directory / "operation.json", packet)
        self.native.state["focused_window"] = {"identifier": "main-window",
                                                 "role": "AXLayoutArea",
                                                 "title": "Character Animator"}
        self.native.state["windows"] = [{"identifier": "main-window", "role": "AXWindow",
                                          "title": "Character Animator", "modal": False}]
        self.native.state["fingerprint"] = "closed-save-as"
        return lease, packet

    def pending_submitted_save_as_after_exit(self):
        source = self.root / "workspace/MaraDialogue-v003"
        parent = self.root / "workspace/welcome-complete-v001"
        source.mkdir()
        parent.mkdir()
        (source / "MaraDialogue-v003.chproj").write_bytes(b"source project")
        (source / "Ch Data").mkdir()
        (source / "Ch Data/projectdata.rope").write_bytes(b"source data")
        self.native.state.update(
            bundle_id="com.adobe.Character-Animator.application",
            application="/Applications/Adobe Character Animator 2026/Adobe Character Animator 2026.app",
            pid=29112, launch_time=1793689177.654345, running=True, process_count=1,
            project_roots=[str(source.resolve())], authentication_or_permission_prompt=False)
        lease = self.claim()
        self.native.fail = True
        with self.assertRaises(CharacterUnknown):
            self.client.save_copy("welcome-complete-v001", "MaraWelcomeComplete-v001", lease["lease_id"])
        packet = self.client.read("operation.json")
        packet["last_command"] = "chooser_continue_directory"
        packet["native"]["operation"] = "chooser_continue_directory"
        packet["native"]["result"] = {"status": "native_chooser_submitted", "completion": "unverified"}
        packet["native"]["state"].update(
            bundle_id="com.adobe.Character-Animator.application",
            application="/Applications/Adobe Character Animator 2026/Adobe Character Animator 2026.app",
            pid=29112, launch_time=1793689177.654345,
            project_roots=[str(source.resolve())], authentication_or_permission_prompt=False)
        write_json(self.client.directory / "operation.json", packet)
        self.native.state = {
            "bundle_id": "com.adobe.Character-Animator.application",
            "application": "/Applications/Adobe Character Animator 2026/Adobe Character Animator 2026.app",
            "running": False, "process_count": 0, "fingerprint": "character-app-stopped",
            "authentication_or_permission_prompt": False,
        }
        return lease, packet, source, parent / "MaraWelcomeComplete-v001"

    def pending_dispatched_save_as_after_exit(self):
        lease, packet, source, destination = self.pending_submitted_save_as_after_exit()
        packet["last_command"] = None
        packet["native"]["operation"] = "save_as"
        packet["native"]["result"] = {"status": "native_command_dispatched",
                                        "completion": "unverified", "retry_safe": False}
        write_json(self.client.directory / "operation.json", packet)
        return lease, packet, source, destination

    def closed_character_project(self):
        source = self.root / "workspace/ClosedProject"
        (source / "Ch Data").mkdir(parents=True)
        (source / "ClosedProject.chproj").write_bytes(b"closed project bytes")
        (source / "Ch Data/projectdata.rope").write_bytes(b"closed project data")
        (source / "Ch Data/repo.noindex").mkdir()
        (source / "Ch Data/repo.noindex/interprocess.lock").write_bytes(b"")
        self.native.state.update(
            bundle_id="com.adobe.Character-Animator.application",
            application="/Applications/Adobe Character Animator 2026/Adobe Character Animator 2026.app",
            running=False, process_count=0, pid=None, launch_time=None,
            authentication_or_permission_prompt=False,
            fingerprint="character-stopped")
        self.native.state.pop("project_roots", None)
        return source.resolve()

    def pending_unknown_import(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        (self.root / "workspace/voice.wav").write_bytes(b"fixture WAV bytes")
        self.native.fail_import_before_envelope = True
        with self.assertRaises(CharacterUnknown):
            self.client.import_media("voice.wav", "audio_wav", lease["lease_id"])
        return lease, self.client.inspect()["pending_operation"]

    def test_copy_uses_fresh_observation_and_retains_source_identity_limits(self):
        lease = self.claim()
        result = self.client.save_copy("copies", "Owned", lease["lease_id"])
        self.assertEqual(result["status"], "owned_copy")
        self.assertIsNone(result["original_source_hash"])
        self.assertEqual(len(result["files"]), 2)
        dispatches = [call for call in self.native.calls
                      if call in ("save_as", "chooser_destination", "chooser_continue_directory")]
        self.assertEqual(dispatches, ["save_as", "chooser_destination", "chooser_continue_directory"])
        self.assertIsNone(self.client.inspect()["pending_operation"])
        self.assertNotIn("lease_id", self.client.session("inspect"))

    def test_foreign_stale_and_replaced_project_leases_reject_before_dispatch(self):
        lease = self.claim()
        with self.assertRaises(CharacterRejected):
            self.client.save_copy("copies", "Wrong", "foreign")
        with self.assertRaises(CharacterRejected):
            self.client.session("claim", "other", lease["project_id"])
        self.native.state["project_roots"] = ["/another/project"]
        with self.assertRaises(CharacterRejected):
            self.client.save_copy("copies", "Changed", lease["lease_id"])
        self.assertNotIn("save_as", self.native.calls)

    def test_directory_navigation_and_save_are_separate_observed_steps(self):
        self.native.directory_transition = True
        lease = self.claim()
        result = self.client.save_copy("copies", "Owned", lease["lease_id"])
        self.assertEqual(result["status"], "owned_copy")
        self.assertEqual(self.native.calls.count("save_as"), 1)
        self.assertEqual(self.native.calls.count("chooser_continue_directory"), 2)
        self.assertIsNone(self.client.inspect()["pending_operation"])

    def test_copy_and_resume_activate_background_window_then_recheck_exact_chooser(self):
        self.native.hide_chooser_after_command = True
        lease = self.claim()
        copied = self.client.save_copy("copies", "Owned", lease["lease_id"])
        self.assertEqual(copied["status"], "owned_copy")
        self.assertEqual(self.native.calls.count("focus"), 1)
        self.client.session("release", lease["owner"], copied["project_id"], lease["lease_id"])

        lease, packet = self.pending_save_as()
        self.native.fail = False
        self.native.state.update(frontmost=False, fingerprint="background-resume",
                                 focused_window={"identifier": "main-window", "role": "AXLayoutArea",
                                                 "title": "Character Animator"},
                                 windows=[{"identifier": "main-window", "role": "AXWindow",
                                           "title": "Character Animator", "modal": False}])
        result = self.client.operation_control("continue_copy", packet["operation_id"], lease["lease_id"])
        self.assertEqual(result["status"], "owned_copy")
        self.assertEqual(self.native.calls.count("focus"), 3)
        self.assertEqual(self.native.calls.count("save_as"), 2)

    def test_failed_save_after_navigation_retains_pending_and_blocks_replay(self):
        self.native.directory_transition = True
        self.native.fail_submit = True
        lease = self.claim()
        with self.assertRaises(CharacterUnknown):
            self.client.save_copy("copies", "Unknown", lease["lease_id"])
        self.assertEqual(self.client.inspect()["pending_operation"]["state"], "unknown")
        self.assertFalse((self.root / "workspace/copies/Unknown").exists())
        before = list(self.native.calls)
        with self.assertRaises(CharacterRejected):
            self.client.save_copy("copies", "Again", lease["lease_id"])
        self.assertEqual(self.native.calls.count("save_as"), before.count("save_as"))

    def test_failed_continuation_retains_unknown_and_blocks_new_mutation(self):
        lease = self.claim()
        self.native.fail = True
        with self.assertRaises(CharacterUnknown):
            self.client.save_copy("copies", "Unknown", lease["lease_id"])
        self.assertEqual(self.client.inspect()["pending_operation"]["state"], "unknown")
        before = self.native.calls.count("save_as")
        with self.assertRaises(CharacterRejected):
            self.client.save_copy("copies", "Again", lease["lease_id"])
        self.assertEqual(self.native.calls.count("save_as"), before)

    def test_cancel_unsubmitted_rejects_wrong_lease_changed_root_and_existing_output(self):
        lease, packet = self.pending_save_as()
        operation_id = packet["operation_id"]
        with self.assertRaises(CharacterRejected):
            self.client.operation_control("cancel_unsubmitted", operation_id, "foreign")
        self.assertEqual(self.client.inspect()["pending_operation"]["state"], "unknown")

        self.native.state["project_roots"] = ["/changed/project"]
        with self.assertRaises(CharacterRejected):
            self.client.operation_control("cancel_unsubmitted", operation_id, lease["lease_id"])
        self.native.state["project_roots"] = [str(self.root / "original")]

        (self.root / "workspace/copies/Recovery").mkdir()
        with self.assertRaises(CharacterRejected):
            self.client.operation_control("cancel_unsubmitted", operation_id, lease["lease_id"])
        self.assertEqual(self.client.inspect()["pending_operation"]["state"], "unknown")

    def test_cancel_unsubmitted_archives_verified_cancel_and_keeps_no_pending_journal(self):
        lease, packet = self.pending_save_as()
        result = self.client.operation_control("cancel_unsubmitted", packet["operation_id"],
                                               lease["lease_id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(self.client.inspect()["pending_operation"])
        archived = self.client.read("operations/" + packet["operation_id"] + ".json")
        self.assertEqual(archived["state"], "resolved")
        self.assertEqual(archived["resolution"], "cancel_unsubmitted")
        self.assertIn("chooser_cancel", self.native.calls)

    def test_continue_copy_resumes_exact_pending_chooser_without_replaying_save_as(self):
        lease, packet = self.pending_save_as()
        self.native.fail = False
        before_save_as = self.native.calls.count("save_as")
        result = self.client.operation_control("continue_copy", packet["operation_id"], lease["lease_id"])
        self.assertEqual(result["status"], "owned_copy")
        self.assertEqual(result["project_root"], str(self.client.path("copies/Recovery")))
        self.assertEqual(self.native.calls.count("save_as"), before_save_as)
        self.assertIsNone(self.client.inspect()["pending_operation"])
        archived = self.client.read("operations/" + packet["operation_id"] + ".json")
        self.assertEqual(archived["resolution"], "continue_copy")
        self.assertEqual(self.client.read("lease.json")["owned_root"], result["project_root"])

    def test_import_media_stages_hash_pinned_psd_under_the_owned_project_lease(self):
        self.native.hide_chooser_after_command = True
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        source = self.root / "workspace/puppet.psd"
        source.write_bytes(b"fixture PSD bytes")
        before_import = list(self.native.calls)
        result = self.client.import_media("puppet.psd", "puppet_psd", lease["lease_id"])
        self.assertEqual(result["status"], "native_import_submitted")
        self.assertEqual(result["completion"], "unverified")
        self.assertEqual(result["source"]["sha256"], result["import_copy"]["sha256"])
        staged = Path(result["import_copy"]["path"])
        self.assertEqual(staged.name, source.name)
        self.assertEqual(staged.parent.parent.name, "inputs")
        self.assertEqual(staged.read_bytes(), source.read_bytes())
        self.assertEqual(self.native.calls[len(before_import):].count("import"), 1)
        self.assertEqual(self.native.calls[len(before_import):].count("chooser_destination"), 1)
        self.assertIsNone(self.client.inspect()["pending_operation"])

    def test_import_adopts_exact_existing_import_chooser_without_menu_replay(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        source = self.root / "workspace/voice.wav"
        source.write_bytes(b"fixture WAV bytes")
        self.native.state["fingerprint"] = "existing-import"
        self.native.state["focused_window"] = {"identifier": "open-panel", "title": "Import",
                                                "modal": True}
        self.native.state["windows"] = [{"identifier": "open-panel", "title": "Import",
                                         "modal": True}]
        calls_before = list(self.native.calls)

        result = self.client.import_media("voice.wav", "audio_wav", lease["lease_id"])

        self.assertEqual(result["status"], "native_import_submitted")
        self.assertEqual(self.native.calls[len(calls_before):].count("import"), 0)
        self.assertEqual(self.native.calls[len(calls_before):].count("chooser_destination"), 1)
        archived = self.client.read("operations/" + result["operation_id"] + ".json")
        self.assertEqual(archived["initial_native_operation"], "inspect_existing_import")

    def test_import_adopts_go_to_sheet_only_under_observed_import_panel(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        (self.root / "workspace/voice.wav").write_bytes(b"fixture WAV bytes")
        self.native.state["fingerprint"] = "existing-import-go-to"
        self.native.state["focused_window"] = {"identifier": "GoToWindow", "role": "AXSheet",
                                                "modal": True}
        self.native.state["windows"] = [
            {"identifier": "open-panel", "title": "Import", "modal": True},
            {"identifier": "GoToWindow", "role": "AXSheet", "modal": True},
        ]
        calls_before = list(self.native.calls)

        result = self.client.import_media("voice.wav", "audio_wav", lease["lease_id"])

        self.assertEqual(result["status"], "native_import_submitted")
        self.assertEqual(self.native.calls[len(calls_before):].count("import"), 0)
        self.assertEqual(self.native.calls[len(calls_before):].count("chooser_continue_directory"), 1)

    def test_import_rejects_unparented_go_to_sheet(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        (self.root / "workspace/voice.wav").write_bytes(b"fixture WAV bytes")
        self.native.state["focused_window"] = {"identifier": "GoToWindow", "role": "AXSheet",
                                                "modal": True}
        self.native.state["windows"] = [{"identifier": "GoToWindow", "role": "AXSheet", "modal": True}]

        with self.assertRaisesRegex(CharacterRejected, "unrelated native modal"):
            self.client.import_media("voice.wav", "audio_wav", lease["lease_id"])
        self.assertNotIn("import", self.native.calls)

    def test_import_rejects_foreign_modal_without_menu_dispatch(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        (self.root / "workspace/voice.wav").write_bytes(b"fixture WAV bytes")
        self.native.state["focused_window"] = {"identifier": "save-panel", "title": "Save Project As",
                                                "modal": True}
        self.native.state["windows"] = [{"identifier": "save-panel", "title": "Save Project As",
                                         "modal": True}]
        calls_before = list(self.native.calls)

        with self.assertRaisesRegex(CharacterRejected, "unrelated native modal"):
            self.client.import_media("voice.wav", "audio_wav", lease["lease_id"])

        self.assertEqual(self.native.calls[len(calls_before):].count("import"), 0)
        self.assertIsNone(self.client.inspect()["pending_operation"])

    def test_scene_export_submits_exact_native_chooser_with_new_directory(self):
        self.native.hide_chooser_after_command = True
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        (self.root / "workspace/exports").mkdir()
        self.native.state["fingerprint"] = "c" * 64
        fingerprint = self.client.inspect()["native"]["state"]["fingerprint"]
        calls_before = list(self.native.calls)

        result = self.client.scene_export("exports/talk", "SignalTalk", fingerprint, lease["lease_id"])

        self.assertEqual(result["status"], "native_export_submitted")
        self.assertEqual(result["completion"], "unverified")
        self.assertEqual(result["expected_output_directory"],
                         str(self.client.path("exports/talk/SignalTalk")))
        self.assertTrue((self.root / "workspace/exports/talk").is_dir())
        self.assertEqual(self.native.calls[len(calls_before):].count("export_png_wav"), 1)
        self.assertEqual(self.native.calls[len(calls_before):].count("chooser_destination"), 1)
        self.assertEqual(self.native.calls[len(calls_before):].count("chooser_continue_directory"), 2)
        self.assertIsNone(self.client.inspect()["pending_operation"])

    def test_continue_export_resumes_exact_unknown_export_chooser_without_menu_replay(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        (self.root / "workspace/exports").mkdir()
        self.native.state["fingerprint"] = "d" * 64
        fingerprint = self.client.inspect()["native"]["state"]["fingerprint"]
        self.native.fail_export_submit = True
        with self.assertRaises(CharacterUnknown):
            self.client.scene_export("exports/talk", "SignalTalk", fingerprint, lease["lease_id"])
        packet = self.client.inspect()["pending_operation"]
        self.assertEqual(packet["operation"], "export_png_wav")
        operation_id = packet["operation_id"]
        self.assertEqual(packet["last_command"], "chooser_continue_directory")
        self.assertEqual(packet["native"]["result"]["status"], "native_directory_requested")
        self.native.fail_export_submit = False
        self.native.state.update(frontmost=False, fingerprint="background-resume",
                                 focused_window={"identifier": "main-window", "role": "AXLayoutArea",
                                                 "title": "Character Animator"},
                                 windows=[{"identifier": "main-window", "role": "AXWindow",
                                           "title": "Character Animator", "modal": False}])
        parent = {"frontmost": True,
                  "focused_window": {"identifier": "save-panel", "title": "Export Scene", "modal": True},
                  "windows": [{"identifier": "save-panel", "title": "Export Scene", "modal": True}]}
        go_to = {"frontmost": True,
                 "focused_window": {"identifier": "GoToWindow", "role": "AXSheet", "modal": True},
                 "windows": [{"identifier": "save-panel", "title": "Export Scene", "modal": True},
                             {"identifier": "GoToWindow", "role": "AXSheet", "modal": True}]}
        self.native.export_inspect_states = [
            {**parent, "fingerprint": "export-parent-initial", "windows": [*parent["windows"],
                 {"identifier": "main-window", "title": "Character Animator"}]},
            {**go_to, "fingerprint": "export-go-to-transition"},
            {**parent, "fingerprint": "export-parent-settled"},
            {**parent, "fingerprint": "export-parent-settled"},
        ]
        destinations_before = self.native.calls.count("chooser_destination")
        continues_before = self.native.calls.count("chooser_continue_directory")
        calls_before = len(self.native.calls)

        result = self.client.operation_control("continue_export", operation_id, lease["lease_id"])

        self.assertEqual(result["status"], "native_export_submitted")
        self.assertEqual(result["completion"], "unverified")
        self.assertEqual(self.native.calls.count("export_png_wav"), 1)
        self.assertEqual(self.native.calls.count("chooser_destination"), destinations_before)
        self.assertEqual(self.native.calls.count("chooser_continue_directory"), continues_before + 1)
        self.assertEqual(self.native.calls[calls_before:][:5], ["inspect", "inspect", "inspect",
                                                               "inspect", "chooser_continue_directory"])
        self.assertIsNone(self.client.inspect()["pending_operation"])
        archived = self.client.read("operations/" + operation_id + ".json")
        self.assertEqual(archived["resolution"], "continue_export")

    def test_continue_export_rejects_changed_process_or_source_before_dispatch(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        (self.root / "workspace/exports").mkdir()
        self.native.state["fingerprint"] = "f" * 64
        fingerprint = self.client.inspect()["native"]["state"]["fingerprint"]
        self.native.fail_export_submit = True
        with self.assertRaises(CharacterUnknown):
            self.client.scene_export("exports/talk", "SignalTalk", fingerprint, lease["lease_id"])
        packet = self.client.inspect()["pending_operation"]
        operation_id = packet["operation_id"]
        self.native.fail_export_submit = False
        baseline = copy.deepcopy(self.native.state)
        destinations_before = self.native.calls.count("chooser_destination")
        continues_before = self.native.calls.count("chooser_continue_directory")

        self.native.state["pid"] += 1
        with self.assertRaises(CharacterRejected):
            self.client.operation_control("continue_export", operation_id, lease["lease_id"])
        self.native.state = copy.deepcopy(baseline)

        self.native.state["project_roots"] = [str(self.root / "original/changed")]
        with self.assertRaises(CharacterRejected):
            self.client.operation_control("continue_export", operation_id, lease["lease_id"])

        self.assertEqual(self.native.calls.count("chooser_destination"), destinations_before)
        self.assertEqual(self.native.calls.count("chooser_continue_directory"), continues_before)
        self.assertEqual(self.client.inspect()["pending_operation"]["operation_id"], operation_id)

    def test_continue_export_resumes_recorded_initial_menu_acknowledgment(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        (self.root / "workspace/exports").mkdir()
        self.native.state["fingerprint"] = "9" * 64
        fingerprint = self.client.inspect()["native"]["state"]["fingerprint"]
        self.native.fail_export_destination = True
        with self.assertRaises(CharacterUnknown):
            self.client.scene_export("exports/talk", "SignalTalk", fingerprint, lease["lease_id"])
        packet = self.client.inspect()["pending_operation"]
        self.assertEqual(packet["native"]["operation"], "export_png_wav")
        self.assertEqual(packet["native"]["result"]["status"], "native_command_dispatched")
        self.assertIsNone(packet.get("last_command"))

        self.native.fail_export_destination = False
        destinations_before = self.native.calls.count("chooser_destination")
        continuations_before = self.native.calls.count("chooser_continue_directory")
        result = self.client.operation_control("continue_export", packet["operation_id"],
                                               lease["lease_id"])

        self.assertEqual(result["status"], "native_export_submitted")
        self.assertEqual(self.native.calls.count("export_png_wav"), 1)
        self.assertEqual(self.native.calls.count("chooser_destination"), destinations_before + 1)
        self.assertEqual(self.native.calls.count("chooser_continue_directory"), continuations_before + 2)

    def test_cancel_unsubmitted_can_cancel_exact_export_panel(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        (self.root / "workspace/exports").mkdir()
        self.native.state["fingerprint"] = "e" * 64
        fingerprint = self.client.inspect()["native"]["state"]["fingerprint"]
        self.native.fail_export_submit = True
        with self.assertRaises(CharacterUnknown):
            self.client.scene_export("exports/talk", "SignalTalk", fingerprint, lease["lease_id"])
        packet = self.client.inspect()["pending_operation"]
        packet["intent"].pop("output_directory_identity")
        write_json(self.client.directory / "operation.json", packet)

        result = self.client.operation_control("cancel_unsubmitted", packet["operation_id"],
                                               lease["lease_id"])

        self.assertEqual(result["completion"], "unsubmitted_export_cancelled")
        self.assertEqual(self.native.calls.count("chooser_cancel"), 1)
        self.assertIsNone(self.client.inspect()["pending_operation"])

    def test_import_media_rejects_wrong_extension_before_native_dispatch(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        source = self.root / "workspace/puppet.png"
        source.write_bytes(b"not a PSD")
        before = list(self.native.calls)
        with self.assertRaises(CharacterRejected):
            self.client.import_media("puppet.png", "puppet_psd", lease["lease_id"])
        self.assertEqual(self.native.calls, before)

    def test_scene_action_requires_exact_inspected_state_and_archives_unverified_dispatch(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        self.native.state["fingerprint"] = "a" * 64
        inspected = self.client.inspect()
        fingerprint = inspected["native"]["state"]["fingerprint"]
        result = self.client.scene_action("add_to_new_scene", fingerprint, lease["lease_id"])
        self.assertEqual(result["status"], "native_scene_action_submitted")
        self.assertEqual(result["completion"], "unverified")
        self.assertEqual(result["expected_state_fingerprint"], fingerprint)
        self.assertEqual(self.native.calls.count("add_to_new_scene"), 1)
        self.assertIsNone(self.client.inspect()["pending_operation"])

    def test_scene_action_rejects_stale_fingerprint_before_native_dispatch(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        before_actions = self.native.calls.count("compute_lip_sync")
        with self.assertRaisesRegex(CharacterRejected, "state changed"):
            self.client.scene_action("compute_lip_sync", "0" * 64, lease["lease_id"])
        self.assertEqual(self.native.calls.count("compute_lip_sync"), before_actions)

    def test_project_selection_is_pinned_to_fresh_state_and_owned_lease(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        self.native.state["fingerprint"] = "b" * 64
        fingerprint = self.client.inspect()["native"]["state"]["fingerprint"]
        result = self.client.project_select("TestPuppet", "puppet", False,
                                            fingerprint, lease["lease_id"])
        self.assertEqual(result["status"], "native_project_item_selected")
        self.assertEqual(result["name"], "TestPuppet")
        self.assertEqual(result["selection_evidence"]["ocr_identity"], "fixture")
        self.assertEqual(self.native.calls.count("project_select"), 1)
        self.assertIsNone(self.client.inspect()["pending_operation"])

    def test_project_selection_requires_strict_extend_and_current_fingerprint(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        before = self.native.calls.count("project_select")
        with self.assertRaisesRegex(CharacterRejected, "strict boolean"):
            self.client.project_select("Voice", "audio", 1, "c" * 64, lease["lease_id"])
        with self.assertRaisesRegex(CharacterRejected, "state changed"):
            self.client.project_select("Voice", "audio", True, "c" * 64, lease["lease_id"])
        self.assertEqual(self.native.calls.count("project_select"), before)

    def test_import_retains_unknown_when_native_chooser_never_submits(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        source = self.root / "workspace/voice.wav"
        source.write_bytes(b"fixture WAV bytes")
        self.native.import_directory_pending = True
        with self.assertRaisesRegex(CharacterUnknown, "was not submitted"):
            self.client.import_media("voice.wav", "audio_wav", lease["lease_id"])
        pending = self.client.inspect()["pending_operation"]
        self.assertEqual(pending["operation"], "import")
        self.assertEqual(pending["state"], "unknown")
        self.assertEqual(self.native.calls.count("import"), 1)
        operation_id = pending["operation_id"]
        self.native.import_directory_pending = False
        self.native.state.update(frontmost=False, fingerprint="background-resume",
                                 focused_window={"identifier": "main-window", "role": "AXLayoutArea",
                                                 "title": "Character Animator"},
                                 windows=[{"identifier": "main-window", "role": "AXWindow",
                                           "title": "Character Animator", "modal": False}])
        resumed = self.client.operation_control("continue_import", operation_id, lease["lease_id"])
        self.assertEqual(resumed["status"], "native_import_submitted")
        self.assertEqual(self.native.calls.count("import"), 1)
        self.assertIsNone(self.client.inspect()["pending_operation"])
        self.assertEqual(self.client.read("operations/" + operation_id + ".json")["resolution"],
                         "continue_import")

    def test_import_recovers_unknown_without_native_envelope_from_exact_live_chooser(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        (self.root / "workspace/voice.wav").write_bytes(b"fixture WAV bytes")
        chooser_calls_before_import = self.native.calls.count("chooser_destination")
        self.native.fail_import_before_envelope = True

        with self.assertRaises(CharacterUnknown):
            self.client.import_media("voice.wav", "audio_wav", lease["lease_id"])

        pending = self.client.inspect()["pending_operation"]
        self.assertEqual(pending["state"], "unknown")
        self.assertNotIn("native", pending)
        self.assertEqual(pending["before_native"]["state"]["pid"], 42)
        self.assertEqual(pending["before_native"]["state"]["launch_time"], "fixture")
        operation_id = pending["operation_id"]

        self.native.fail_import_before_envelope = False
        resumed = self.client.operation_control("continue_import", operation_id, lease["lease_id"])

        self.assertEqual(resumed["status"], "native_import_submitted")
        self.assertEqual(self.native.calls.count("import"), 1)
        self.assertEqual(self.native.calls.count("chooser_destination"), chooser_calls_before_import + 1)
        self.assertIsNone(self.client.inspect()["pending_operation"])

    def test_import_recovery_keeps_unknown_when_owned_root_changed(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        (self.root / "workspace/voice.wav").write_bytes(b"fixture WAV bytes")
        chooser_calls_before_import = self.native.calls.count("chooser_destination")
        self.native.fail_import_before_envelope = True

        with self.assertRaises(CharacterUnknown):
            self.client.import_media("voice.wav", "audio_wav", lease["lease_id"])
        pending = self.client.inspect()["pending_operation"]
        operation_id = pending["operation_id"]

        self.native.state["project_roots"] = [str(self.root / "foreign-project")]
        with self.assertRaisesRegex(CharacterRejected, "same owned project"):
            self.client.operation_control("continue_import", operation_id, lease["lease_id"])

        retained = self.client.inspect()["pending_operation"]
        self.assertEqual(retained["operation_id"], operation_id)
        self.assertEqual(retained["state"], "unknown")
        self.assertEqual(self.native.calls.count("import"), 1)
        self.assertEqual(self.native.calls.count("chooser_destination"), chooser_calls_before_import)

    def test_continue_import_archives_exact_recorded_pre_menu_failure(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        (self.root / "workspace/voice.wav").write_bytes(b"fixture WAV bytes")
        self.native.fail_import_focus_dispatch = True
        with self.assertRaises(CharacterUnknown):
            self.client.import_media("voice.wav", "audio_wav", lease["lease_id"])
        packet = self.client.inspect()["pending_operation"]
        self.assertNotIn("native", packet)
        self.assertNotIn("last_command", packet)
        self.assertEqual(packet["failure"], {
            "code": "OUTCOME_UNKNOWN",
            "message": "[focus_dispatch] Character Animator did not become foreground through Accessibility"})
        import_calls = self.native.calls.count("import")
        chooser_calls = self.native.calls.count("chooser_destination")

        result = self.client.operation_control("continue_import", packet["operation_id"], lease["lease_id"])

        self.assertEqual(result["status"], "rejected_before_menu_dispatch")
        self.assertEqual(result["basis"], "recorded_native_pre_menu_failure")
        self.assertEqual(self.native.calls.count("import"), import_calls)
        self.assertEqual(self.native.calls.count("chooser_destination"), chooser_calls)
        self.assertIsNone(self.client.inspect()["pending_operation"])
        archived = self.client.read("operations/" + packet["operation_id"] + ".json")
        self.assertEqual(archived["resolution"], "rejected_before_menu_dispatch")
        self.assertEqual(archived["failure"], packet["failure"])
        self.assertEqual(archived["reconciliation_basis"], "recorded_native_pre_menu_failure")
        self.assertNotIn("native", archived)
        self.assertIn("reconciliation_native", archived)

    def test_continue_import_does_not_resolve_generic_unknown_without_chooser(self):
        lease = self.claim()
        self.client.save_copy("copies", "Owned", lease["lease_id"])
        (self.root / "workspace/voice.wav").write_bytes(b"fixture WAV bytes")
        self.native.fail_import_generic_no_menu = True
        with self.assertRaises(CharacterUnknown):
            self.client.import_media("voice.wav", "audio_wav", lease["lease_id"])
        packet = self.client.inspect()["pending_operation"]
        import_calls = self.native.calls.count("import")
        chooser_calls = self.native.calls.count("chooser_destination")

        with self.assertRaisesRegex(CharacterRejected, "exact existing Import or Go To chooser"):
            self.client.operation_control("continue_import", packet["operation_id"], lease["lease_id"])

        retained = self.client.inspect()["pending_operation"]
        self.assertEqual(retained["operation_id"], packet["operation_id"])
        self.assertEqual(retained["state"], "unknown")
        self.assertEqual(self.native.calls.count("import"), import_calls)
        self.assertEqual(self.native.calls.count("chooser_destination"), chooser_calls)

    def test_confirm_cancelled_import_archives_user_report_without_native_claim(self):
        lease, packet = self.pending_unknown_import()
        self.native.state["focused_window"] = {"identifier": "main-window", "title": "Character Animator"}
        self.native.state["windows"] = []
        native_calls = list(self.native.calls)

        result = self.client.operation_control(
            "confirm_cancelled_import", packet["operation_id"], lease["lease_id"],
            user_confirmed_cancelled=True)

        self.assertEqual(result["status"], "cancelled_user_reported")
        self.assertEqual(result["completion"], "user_reported_native_cancellation_unverified")
        self.assertEqual(self.native.calls.count("import"), native_calls.count("import"))
        self.assertEqual(self.native.calls.count("chooser_destination"), native_calls.count("chooser_destination"))
        self.assertIsNone(self.client.inspect()["pending_operation"])
        archived = self.client.read("operations/" + packet["operation_id"] + ".json")
        self.assertEqual(archived["resolution"], "user_reported_cancellation")
        self.assertTrue(archived["user_confirmed_cancelled"])
        self.assertEqual(archived["before_native"], packet["before_native"])
        self.assertIn("cancellation_native", archived)
        self.assertEqual(archived["failure"], packet["failure"])

    def test_confirm_cancelled_import_rejects_missing_attestation_modal_root_or_changed_hash(self):
        lease, packet = self.pending_unknown_import()
        operation_id = packet["operation_id"]

        def confirm(attested=True):
            return self.client.operation_control(
                "confirm_cancelled_import", operation_id, lease["lease_id"],
                user_confirmed_cancelled=attested)

        with self.assertRaisesRegex(CharacterRejected, "user reports"):
            confirm(False)
        with self.assertRaisesRegex(CharacterRejected, "no visible modal"):
            confirm()
        self.native.state["focused_window"] = {"identifier": "main-window", "title": "Character Animator"}
        self.native.state["windows"] = []
        self.native.state["project_roots"] = [str(self.root / "foreign-project")]
        with self.assertRaisesRegex(CharacterRejected, "same owned project"):
            confirm()
        self.native.state["project_roots"] = [packet["parameters"]["project_root"]]
        (self.root / "workspace/voice.wav").write_bytes(b"fixture WAV bytex")
        with self.assertRaisesRegex(CharacterRejected, "hashes differ"):
            confirm()

        retained = self.client.inspect()["pending_operation"]
        self.assertEqual(retained["operation_id"], operation_id)
        self.assertEqual(retained["state"], "unknown")
        self.assertEqual(self.native.calls.count("import"), 1)

    def test_resume_renews_expired_lease_for_exact_unknown_operation_without_dispatch(self):
        lease, packet = self.pending_unknown_import()
        original_packet = copy.deepcopy(packet)
        lease_path = self.client.directory / "lease.json"
        old_lease = self.client.read("lease.json")
        old_lease["expires_at"] = time.time() - 1
        write_json(lease_path, old_lease)
        native_calls = list(self.native.calls)

        resumed = self.client.session("resume", lease["owner"], packet["before_project_id"],
                                      lease["lease_id"], 300,
                                      pending_operation_id=packet["operation_id"])

        self.assertEqual(resumed["state"], "owned")
        self.assertNotEqual(resumed["lease_id"], lease["lease_id"])
        self.assertEqual(resumed["pending_operation"], original_packet)
        self.assertEqual(self.native.calls.count("import"), native_calls.count("import"))
        renewed = self.client.read("lease.json")
        self.assertEqual(renewed["owner"], lease["owner"])
        self.assertEqual(renewed["owned_root"], old_lease["owned_root"])
        self.assertEqual(renewed["lease_id"], resumed["lease_id"])
        self.assertGreater(renewed["expires_at"], time.time())
        self.assertEqual(self.client.read("operation.json"), original_packet)

    def test_resume_renews_expired_lease_for_exact_initial_save_as_without_dispatch(self):
        source = self.root / "workspace/SignalSparkHomeport"
        source.mkdir()
        self.native.state["project_roots"] = [str(source.resolve())]
        lease, packet = self.pending_save_as()
        original_packet = copy.deepcopy(packet)
        lease_path = self.client.directory / "lease.json"
        old_lease = self.client.read("lease.json")
        self.assertIsNone(old_lease["owned_root"])
        old_lease["expires_at"] = time.time() - 1
        write_json(lease_path, old_lease)
        native_calls = list(self.native.calls)

        resumed = self.client.session("resume", lease["owner"], packet["before_project_id"],
                                      lease["lease_id"], 300,
                                      pending_operation_id=packet["operation_id"])

        self.assertEqual(resumed["state"], "owned")
        self.assertNotEqual(resumed["lease_id"], lease["lease_id"])
        self.assertEqual(resumed["pending_operation"], original_packet)
        self.assertEqual(self.native.calls.count("save_as"), native_calls.count("save_as"))
        renewed = self.client.read("lease.json")
        self.assertEqual(renewed["owned_root"], None)
        self.assertEqual(renewed["lease_id"], resumed["lease_id"])
        self.assertEqual(self.client.read("operation.json"), original_packet)

    def test_resume_rejects_mismatched_initial_save_as_intent_or_source(self):
        source = self.root / "workspace/SignalSparkHomeport"
        source.mkdir()
        self.native.state["project_roots"] = [str(source.resolve())]
        lease, packet = self.pending_save_as()
        lease_path = self.client.directory / "lease.json"
        old_lease = self.client.read("lease.json")
        old_lease["expires_at"] = time.time() - 1
        write_json(lease_path, old_lease)
        original_native_state = copy.deepcopy(self.native.state)
        original_calls = self.native.calls.count("save_as")

        cases = (
            ("wrong operation", lambda damaged: damaged.update(operation="import"), None),
            ("wrong destination", lambda damaged: damaged["intent"].update(
                destination=str(self.root / "workspace/copies/Other")), None),
            ("wrong source root", lambda damaged: damaged["parameters"].update(
                project_root=str(self.root / "workspace/other")), None),
            ("replaced process", lambda damaged: None, "pid"),
        )
        for label, damage_packet, damage_native in cases:
            with self.subTest(case=label):
                damaged = copy.deepcopy(packet)
                damage_packet(damaged)
                write_json(self.client.directory / "operation.json", damaged)
                self.native.state = copy.deepcopy(original_native_state)
                if damage_native == "pid":
                    self.native.state["pid"] += 1
                with self.assertRaises(CharacterRejected):
                    self.client.session("resume", lease["owner"], packet["before_project_id"],
                                        lease["lease_id"], 300,
                                        pending_operation_id=packet["operation_id"])
                self.assertEqual(self.client.read("lease.json"), old_lease)
                self.assertEqual(self.client.read("operation.json"), damaged)
                self.assertEqual(self.native.calls.count("save_as"), original_calls)

    def test_reconcile_closed_initial_save_as_preserves_unsubmitted_receipt_without_cancellation(self):
        lease, packet = self.pending_closed_initial_save_as()
        native_calls = list(self.native.calls)

        result = self.client.operation_control("reconcile_unsubmitted_copy", packet["operation_id"],
                                               lease["lease_id"])

        self.assertEqual(result["status"], "unsubmitted_copy_reconciled")
        self.assertEqual(result["completion"], "recorded_pre_submit_stage_closed_without_submission")
        self.assertFalse(result["expected_output_exists"])
        self.assertEqual(self.native.calls, native_calls + ["inspect", "inspect"])
        self.assertIsNone(self.client.inspect()["pending_operation"])
        archived = self.client.read("operations/" + packet["operation_id"] + ".json")
        self.assertEqual(archived["state"], "resolved")
        self.assertEqual(archived["resolution"], "reconcile_unsubmitted_copy")
        self.assertEqual(archived["reconciliation_basis"], "recorded_pre_submit_directory_request")
        self.assertEqual(archived["native"]["result"]["status"], "native_chooser_directory_requested")
        self.assertNotIn("cancelled", result["status"])
        self.assertIsNone(self.client.read("lease.json")["owned_root"])
        self.assertNotIn("chooser_cancel", self.native.calls[len(native_calls):])

    def test_reconcile_closed_initial_save_as_rejects_submitted_or_changed_state(self):
        lease, packet = self.pending_closed_initial_save_as()
        original_lease = self.client.read("lease.json")
        original_native = copy.deepcopy(self.native.state)
        original_packet = copy.deepcopy(packet)
        destination = Path(packet["intent"]["destination"])
        cases = (
            ("submitted", lambda damaged, state: damaged["native"]["result"].update(
                status="native_chooser_submitted"), False),
            ("later dispatch", lambda damaged, state: damaged.update(
                last_command="chooser_continue_directory"), False),
            ("destination exists", lambda damaged, state: None, True),
            ("changed process", lambda damaged, state: state.update(pid=state["pid"] + 1), False),
            ("changed project", lambda damaged, state: state.update(
                project_roots=[str(self.root / "workspace/other")]), False),
            ("chooser visible", lambda damaged, state: state.update(
                focused_window={"identifier": "GoToWindow", "role": "AXSheet"}), False),
            ("modal visible", lambda damaged, state: state.update(
                windows=[{"identifier": "permission-prompt", "modal": True}]), False),
            ("permission prompt", lambda damaged, state: state.update(
                authentication_or_permission_prompt=True), False),
        )
        for label, damage, create_destination in cases:
            with self.subTest(case=label):
                damaged = copy.deepcopy(original_packet)
                state = copy.deepcopy(original_native)
                damage(damaged, state)
                write_json(self.client.directory / "operation.json", damaged)
                self.native.state = state
                if create_destination:
                    destination.mkdir()
                with self.assertRaises(CharacterRejected):
                    self.client.operation_control("reconcile_unsubmitted_copy", packet["operation_id"],
                                                  lease["lease_id"])
                self.assertEqual(self.client.read("lease.json"), original_lease)
                self.assertEqual(self.client.read("operation.json"), damaged)
                if create_destination:
                    destination.rmdir()

    def test_reconcile_submitted_copy_after_process_exit_archives_no_copy_and_expires_original_lease(self):
        lease, packet, source, destination = self.pending_submitted_save_as_after_exit()
        source_files = {path.relative_to(source): path.read_bytes()
                        for path in source.rglob("*") if path.is_file()}
        expired_lease = self.client.read("lease.json")
        expired_lease["expires_at"] = time.time() - 1
        write_json(self.client.directory / "lease.json", expired_lease)
        native_calls = list(self.native.calls)

        result = self.client.operation_control(
            "reconcile_copy_after_exit", packet["operation_id"], lease["lease_id"])

        self.assertEqual(result["status"], "destination_absent_after_process_exit")
        self.assertEqual(result["completion"], "no_copy_verified")
        self.assertFalse(result["expected_output_exists"])
        self.assertFalse(destination.exists())
        self.assertEqual(self.native.calls, native_calls + ["inspect", "inspect"])
        self.assertIsNone(self.client.read("operation.json"))
        archived = self.client.read("operations/" + packet["operation_id"] + ".json")
        self.assertEqual(archived["state"], "resolved")
        self.assertEqual(archived["resolution"], "reconcile_copy_after_exit")
        self.assertEqual(archived["failure"], packet["failure"])
        self.assertEqual(archived["native"], packet["native"])
        released = self.client.read("lease.json")
        self.assertEqual(released["lease_id"], lease["lease_id"])
        self.assertEqual(released["owner"], lease["owner"])
        self.assertEqual(released["project_id"], packet["before_project_id"])
        self.assertEqual(released["expires_at"], 0)
        self.assertEqual({path.relative_to(source): path.read_bytes()
                          for path in source.rglob("*") if path.is_file()}, source_files)

    def test_reconcile_initial_menu_dispatch_after_exit_records_no_copy_without_submission(self):
        lease, packet, source, destination = self.pending_dispatched_save_as_after_exit()
        result = self.client.operation_control(
            "reconcile_copy_after_exit", packet["operation_id"], lease["lease_id"])

        self.assertEqual(result["status"], "destination_absent_after_process_exit")
        self.assertEqual(result["completion"], "no_copy_verified")
        self.assertFalse(destination.exists())
        self.assertEqual(self.native.calls[-2:], ["inspect", "inspect"])
        archived = self.client.read("operations/" + packet["operation_id"] + ".json")
        self.assertEqual(archived["reconciliation_basis"],
                         "menu_dispatched_save_as_process_exited_before_chooser_submission")
        self.assertIsNone(archived["last_command"])
        self.assertEqual(archived["native"]["operation"], "save_as")
        self.assertEqual(archived["native"]["result"]["status"], "native_command_dispatched")
        self.assertIsNone(self.client.read("operation.json"))
        self.assertEqual(self.client.read("lease.json")["expires_at"], 0)
        self.assertTrue(source.is_dir())

    def test_reconcile_initial_menu_dispatch_rejects_wrong_stage_and_running_process(self):
        lease, packet, _, destination = self.pending_dispatched_save_as_after_exit()
        original_lease = self.client.read("lease.json")
        original_packet = copy.deepcopy(packet)
        cases = (
            ("wrong native operation", lambda damaged: damaged["native"].update(operation="chooser_destination")),
            ("submitted status", lambda damaged: damaged["native"]["result"].update(
                status="native_chooser_submitted")),
            ("last command present", lambda damaged: damaged.update(last_command="chooser_destination")),
        )
        for label, damage in cases:
            with self.subTest(case=label):
                damaged = copy.deepcopy(original_packet)
                damage(damaged)
                write_json(self.client.directory / "operation.json", damaged)
                self.native.state.update(running=False, process_count=0, pid=None, launch_time=None,
                                         project_roots=[])
                with self.assertRaises(CharacterRejected):
                    self.client.operation_control("reconcile_copy_after_exit", packet["operation_id"],
                                                  lease["lease_id"])
                self.assertEqual(self.client.read("operation.json"), damaged)
                self.assertEqual(self.client.read("lease.json"), original_lease)
                self.assertFalse(destination.exists())
        write_json(self.client.directory / "operation.json", original_packet)
        self.native.state.update(running=True, process_count=1, pid=29112,
                                 launch_time=1793689177.654345, project_roots=[])
        with self.assertRaisesRegex(CharacterRejected, "app to be stopped"):
            self.client.operation_control("reconcile_copy_after_exit", packet["operation_id"],
                                          lease["lease_id"])
        self.assertEqual(self.client.read("operation.json"), original_packet)
        self.assertEqual(self.client.read("lease.json"), original_lease)

    def test_reconcile_submitted_copy_after_exit_rejects_unsupported_or_changed_evidence(self):
        lease, packet, _, destination = self.pending_submitted_save_as_after_exit()
        original_lease = self.client.read("lease.json")
        original_state = copy.deepcopy(self.native.state)
        original_packet = copy.deepcopy(packet)
        operation_path = self.client.directory / "operation.json"
        lease_path = self.client.directory / "lease.json"

        cases = (
            ("wrong lease", lambda damaged, current_lease, state: None, "foreign"),
            ("wrong operation", lambda damaged, current_lease, state: damaged.update(operation="import"), None),
            ("unsupported stage", lambda damaged, current_lease, state: damaged.update(
                last_command="chooser_destination"), None),
            ("not submitted", lambda damaged, current_lease, state: damaged["native"]["result"].update(
                status="native_directory_requested"), None),
            ("changed source root", lambda damaged, current_lease, state: damaged["native"]["state"].update(
                project_roots=[str(self.root / "workspace/other")]), None),
            ("mismatched lease project", lambda damaged, current_lease, state: current_lease.update(
                project_id="changed-project"), None),
            ("owned lease", lambda damaged, current_lease, state: current_lease.update(
                owned_root=str(self.root / "workspace/other")), None),
            ("app still running", lambda damaged, current_lease, state: state.update(
                running=True, process_count=1, pid=29112, launch_time=1793689177.654345), None),
            ("second app process", lambda damaged, current_lease, state: state.update(
                running=False, process_count=1), None),
            ("permission prompt", lambda damaged, current_lease, state: state.update(
                authentication_or_permission_prompt=True), None),
        )
        for label, damage, requested_lease in cases:
            with self.subTest(case=label):
                damaged = copy.deepcopy(original_packet)
                current_lease = copy.deepcopy(original_lease)
                state = copy.deepcopy(original_state)
                damage(damaged, current_lease, state)
                write_json(operation_path, damaged)
                write_json(lease_path, current_lease)
                self.native.state = state
                with self.assertRaises(CharacterRejected):
                    self.client.operation_control(
                        "reconcile_copy_after_exit", packet["operation_id"],
                        requested_lease or lease["lease_id"])
                self.assertEqual(self.client.read("operation.json"), damaged)
                self.assertEqual(self.client.read("lease.json"), current_lease)
                self.assertFalse(destination.exists())

        write_json(operation_path, original_packet)
        write_json(lease_path, original_lease)
        self.native.state = copy.deepcopy(original_state)
        destination.mkdir()
        with self.assertRaises(CharacterRejected):
            self.client.operation_control(
                "reconcile_copy_after_exit", packet["operation_id"], lease["lease_id"])
        destination.rmdir()
        self.assertEqual(self.client.read("operation.json"), original_packet)
        self.assertEqual(self.client.read("lease.json"), original_lease)

    def test_reconcile_submitted_copy_after_exit_rejects_unstable_absent_process_observation(self):
        lease, packet, _, _ = self.pending_submitted_save_as_after_exit()
        original_lease = self.client.read("lease.json")
        original_packet = copy.deepcopy(packet)
        self.native.export_inspect_states = [
            {"fingerprint": "stopped-first-observation"},
            {"fingerprint": "stopped-second-observation"},
        ]

        with self.assertRaisesRegex(CharacterRejected, "stable stopped-process observations"):
            self.client.operation_control(
                "reconcile_copy_after_exit", packet["operation_id"], lease["lease_id"])

        self.assertEqual(self.client.read("operation.json"), original_packet)
        self.assertEqual(self.client.read("lease.json"), original_lease)

    def test_reconcile_submitted_copy_after_exit_retries_archive_cleanup_after_unlink_failure(self):
        lease, packet, _, _ = self.pending_submitted_save_as_after_exit()
        operation_path = self.client.directory / "operation.json"
        original_unlink = Path.unlink

        def fail_operation_unlink(path, *args, **kwargs):
            if path == operation_path:
                raise OSError("fixture unlink failure")
            return original_unlink(path, *args, **kwargs)

        with (patch.object(Path, "unlink", fail_operation_unlink),
              self.assertRaisesRegex(CharacterUnknown, "pending journal remains")):
            self.client.operation_control(
                "reconcile_copy_after_exit", packet["operation_id"], lease["lease_id"])

        self.assertEqual(self.client.read("operation.json"), packet)
        self.assertEqual(self.client.read("lease.json")["expires_at"], 0)
        archived = self.client.read("operations/" + packet["operation_id"] + ".json")
        self.assertEqual(archived["result"]["completion"], "no_copy_verified")
        retried = self.client.operation_control(
            "reconcile_copy_after_exit", packet["operation_id"], lease["lease_id"])
        self.assertEqual(retried["status"], "destination_absent_after_process_exit")
        self.assertIsNone(self.client.read("operation.json"))

    def test_resume_rejects_active_wrong_owner_operation_or_project_without_changes(self):
        lease, packet = self.pending_unknown_import()
        lease_path = self.client.directory / "lease.json"
        old_lease = self.client.read("lease.json")
        old_lease["expires_at"] = time.time() + 300
        write_json(lease_path, old_lease)

        def resume(**overrides):
            args = {"owner": lease["owner"], "expected_project": packet["before_project_id"],
                    "lease_id": lease["lease_id"], "pending_operation_id": packet["operation_id"]}
            args.update(overrides)
            return self.client.session("resume", args["owner"], args["expected_project"],
                                       args["lease_id"], pending_operation_id=args["pending_operation_id"])

        with self.assertRaises(CharacterRejected):
            resume()
        old_lease["expires_at"] = time.time() - 1
        write_json(lease_path, old_lease)
        with self.assertRaises(CharacterRejected):
            resume(owner="different-owner")
        with self.assertRaises(CharacterRejected):
            resume(pending_operation_id="0" * 32)
        self.native.state["project_roots"] = [str(self.root / "foreign-project")]
        with self.assertRaises(CharacterRejected):
            resume()

        self.assertEqual(self.client.read("lease.json"), old_lease)
        retained = self.client.inspect()["pending_operation"]
        self.assertEqual(retained, packet)
        self.assertEqual(self.native.calls.count("import"), 1)
        self.assertEqual(self.native.calls.count("chooser_destination"), 1)

    def test_verify_copy_accepts_exact_observed_root_and_archives_receipt(self):
        lease, packet = self.pending_save_as()
        destination = self.client.path("copies/Recovery")
        destination.mkdir()
        (destination / "Recovery.chproj").write_bytes(b"fixture project")
        (destination / "Ch Data").mkdir()
        (destination / "Ch Data/projectdata.rope").write_bytes(b"fixture data")
        self.native.state["project_roots"] = [str(destination)]
        self.native.state["fingerprint"] = "verified-copy"

        result = self.client.operation_control("verify_copy", packet["operation_id"], lease["lease_id"])
        self.assertEqual(result["status"], "owned_copy")
        self.assertEqual(result["project_root"], str(destination))
        self.assertEqual(len(result["files"]), 2)
        self.assertIsNone(self.client.inspect()["pending_operation"])
        self.assertEqual(self.client.read("lease.json")["owned_root"], str(destination))

    def test_path_escape_symlink_and_existing_destination_rejected(self):
        with self.assertRaises(CharacterRejected):
            self.client.path("../outside")
        (self.root / "workspace/link").symlink_to(self.root / "workspace/copies", target_is_directory=True)
        with self.assertRaises(CharacterRejected):
            self.client.path("link/Owned")
        with self.assertRaises(CharacterRejected):
            self.client.path("copies", new=True)

    def test_failure_preserves_native_codes_and_unknown_cause(self):
        rejected = failure("project_save_copy", CharacterRejected("stale", "STALE_STATE"))
        self.assertEqual(rejected.structured_content["error"]["code"], "STALE_STATE")
        try:
            try:
                raise FileNotFoundError("post-write fixture")
            except FileNotFoundError as exc:
                raise CharacterUnknown("written but unreadable") from exc
        except CharacterUnknown as exc:
            unknown = failure("project_save_copy", exc)
        self.assertEqual(unknown.structured_content["error"]["outcome"], "unknown")
        self.assertFalse(unknown.structured_content["error"]["retry_safe"])


class CharacterProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_catalog_is_lazy_typed_and_rejects_invalid_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workspace").mkdir()
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "cavi_adobe_mcp.character_animator_server"],
                env={**os.environ, "CAVI_ADOBE_DATA": str(root),
                     "CAVI_ADOBE_WORKSPACE": str(root / "workspace")})
            async with stdio_client(params) as (r, w), ClientSession(r, w) as client:
                initialized = await client.initialize()
                self.assertEqual(initialized.server_info.name, "cavi-character-animator-mcp")
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                for tool in tools.values():
                    self.assertTrue(tool.output_schema["required"], tool.name)
                session_result = tools["session_control"].output_schema["properties"]["result"]["anyOf"]
                self.assertEqual(len(session_result), 2)
                self.assertIn("resume", tools["session_control"].input_schema["properties"]["action"]["enum"])
                self.assertIn("open_prepared_copy",
                              tools["session_control"].input_schema["properties"]["action"]["enum"])
                self.assertIn("pending_operation_id", tools["session_control"].input_schema["properties"])
                self.assertIn("prepared_copy_id", tools["session_control"].input_schema["properties"])
                self.assertIn("project_prepare_copy", tools)
                self.assertIn("prepared_copy_id",
                              tools["project_prepare_copy"].output_schema["properties"])
                self.assertIn("continue_copy", tools["operation_control"].input_schema["properties"]["action"]["enum"])
                self.assertIn("continue_export", tools["operation_control"].input_schema["properties"]["action"]["enum"])
                self.assertIn("reconcile_unsubmitted_copy",
                              tools["operation_control"].input_schema["properties"]["action"]["enum"])
                self.assertIn("reconcile_copy_after_exit",
                              tools["operation_control"].input_schema["properties"]["action"]["enum"])
                self.assertIn("import_media", tools)
                self.assertIn("scene_action", tools)
                self.assertIn("scene_export", tools)
                self.assertIn("project_select", tools)
                self.assertIn("confirm_cancelled_import", tools["operation_control"].input_schema["properties"]["action"]["enum"])
                self.assertIn("user_confirmed_cancelled", tools["operation_control"].input_schema["properties"])
                self.assertFalse((root / "character-animator/native").exists())
                result = await client.call_tool("session_control", {"action": "unknown"})
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["error"]["outcome"], "rejected")
                missing = await client.call_tool("export_inspect", {"recipe": "missing.json"})
                self.assertTrue(missing.is_error)
                self.assertEqual(missing.structured_content["error"]["code"], "INPUT_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
