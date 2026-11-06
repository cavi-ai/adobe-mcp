"""Synthetic Animate protocol fixtures; they are not native application evidence."""

import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

from cavi_adobe_mcp.animate_authoring import (
    APP_ID,
    AnimateAuthoring,
    AnimateRejected,
    AnimateUnknown,
)


class AnimateAuthoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "source").mkdir()
        (self.root / "out").mkdir()
        self.source = self.root / "source/walk.png"
        Image.new("RGBA", (8, 8), (200, 30, 60, 255)).save(self.source)
        self.documents = []
        self.authoring = AnimateAuthoring(self.root, dispatcher=self.dispatch)
        self.authoring.status = lambda: {"running": True, "process": {"pid": 123}}

    @staticmethod
    def local(uri):
        return Path(unquote(urlparse(uri).path))

    def dispatch(self, request, _state):
        action = request["operation"]
        if action == "inspect":
            result = {"operation_id": request["operation_id"], "documents": self.documents}
            return {"version": 1, "operation_id": request["operation_id"], "ok": True, "result": result}
        if action in ("create_import", "open_copy", "publish_open"):
            if action == "publish_open":
                target = self.local(request["document_uri"])
                self.documents = [item for item in self.documents if item["path_uri"] != request["staging_uri"]]
            else:
                uri = request["staging_uri"] if action == "create_import" else request["document_uri"]
                target = self.local(uri)
            if action == "create_import":
                target.write_bytes(b"synthetic editable FLA")
            doc = {"path_uri": target.as_uri(), "name": target.name,
                   "width": 8, "height": 8, "frame_rate": 12.0,
                   "frame_count": 2, "layer_count": 1,
                   "layers": [{"name": "Layer 1", "type": "normal", "frame_count": 2}],
                   "symbols": []}
            self.documents.append(doc)
            result = {"operation_id": request["operation_id"], "document": doc}
            return {"version": 1, "operation_id": request["operation_id"], "ok": True, "result": result}
        if action == "export_frames":
            snapshot = self.local(request["snapshot_uri"])
            snapshot.write_bytes(b"synthetic in-memory FLA snapshot")
            for uri in request["frame_uris"]:
                path = self.local(uri)
                Image.new("RGBA", (8, 8), (20, 40, 90, 255)).save(path)
            doc = next(item for item in self.documents if item["path_uri"] == request["document_uri"])
            result = {"operation_id": request["operation_id"], "document": doc,
                      "snapshot_uri": request["snapshot_uri"],
                      "outputs": [{"index": index, "path_uri": uri}
                                  for index, uri in enumerate(request["frame_uris"])]}
            return {"version": 1, "operation_id": request["operation_id"], "ok": True, "result": result}
        raise AssertionError(action)

    def test_recovery_adopts_completed_copy_without_repeating_export(self):
        imported=self.authoring.create_from_png("source/walk.png","out/walk.fla")
        sid=imported["session"]
        operation="a"*32
        session=self.authoring._session(sid)
        pending={"operation_id":operation,"session_id":sid,"workspace":str(self.root),
                 "session":session,"action":"export_frames","dispatched":True}
        store=self.root/".animate-authoring"
        (store/"pending.json").write_text(json.dumps(pending))
        result=self.authoring.operation_control("inspect",sid,operation)
        self.assertFalse(result["can_reconcile"])
        directory=store/("dispatch-"+operation)
        directory.mkdir()
        (directory/"response.json").write_text(json.dumps({"ok":True,"operation_id":operation,
                                                          "result":{"document":imported["document"]}}))
        result=self.authoring.operation_control("inspect",sid,operation)
        self.assertTrue(result["can_reconcile"])
        with self.assertRaises(ValueError):
            self.authoring.operation_control("adopt_current",sid,operation,"0"*64,result["native_sha256"])
        self.assertTrue((store/"pending.json").exists())
        adopted=self.authoring.operation_control("adopt_current",sid,operation,
                                                result["copy_sha256"],result["native_sha256"])
        self.assertEqual(adopted["state"],"adopted_current")
        self.assertFalse((store/"pending.json").exists())

    def test_create_import_exports_ordered_frames_and_validates_media_receipt(self):
        imported = self.authoring.create_from_png("source/walk.png", "out/walk.fla")
        self.assertEqual(imported["document"]["path_uri"], (self.root / "out/walk.fla").as_uri())
        result = self.authoring.export_frames(imported["session"], "out/walk-frames")
        self.assertEqual(result["media"]["frame_count"], 2)
        self.assertEqual(result["media"]["fps"], {"numerator": 12, "denominator": 1})
        receipt = json.loads((self.root / "out/walk-frames/media.json").read_text())
        self.assertEqual([Path(frame["path"]).name for frame in receipt["frames"]],
                         ["frame_0001.png", "frame_0002.png"])
        self.assertEqual(receipt["schema"], "cavi.animation-media.v1")
        self.assertEqual(receipt["sources"][0]["path"], "out/walk-frames/document-source.fla")
        self.assertTrue((self.root / receipt["sources"][0]["path"]).is_file())
        self.assertEqual(result["visual_acceptance"], "unreviewed")

    def test_inspect_creates_private_state_store_before_native_dispatch(self):
        result = self.authoring.inspect()
        self.assertEqual(result["native"]["documents"], [])
        self.assertIsNone(result["pending_operation"])
        self.assertTrue((self.root / ".animate-authoring/lock").is_file())

    def test_open_copy_owns_only_new_fla_path(self):
        source_fla = self.root / "source/input.fla"
        source_fla.write_bytes(b"source FLA")
        result = self.authoring.open_copy("source/input.fla", "out/copy.fla")
        self.assertEqual(result["document"]["path_uri"], (self.root / "out/copy.fla").as_uri())
        self.assertEqual((self.root / "out/copy.fla").read_bytes(), b"source FLA")
        self.assertEqual(self.authoring._session(result["session"])["state"], "owned")

    def test_create_import_rejects_symlinked_staging_before_writing(self):
        with tempfile.TemporaryDirectory() as outside:
            self.authoring.store.mkdir()
            (self.authoring.store / "staging").symlink_to(outside, target_is_directory=True)
            self.authoring.dispatcher = lambda *_: self.fail("native dispatch must not run")
            with self.assertRaises(AnimateRejected):
                self.authoring.create_from_png("source/walk.png", "out/copy.fla")
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_open_copy_preserves_file_created_after_absence_check(self):
        source = self.root / "source/input.fla"
        source.write_bytes(b"source FLA")
        target = self.root / "out/race.fla"
        original_open = Path.open

        def race_winner(path, mode="r", *args, **kwargs):
            if path == target and mode == "xb":
                with original_open(path, "xb") as stream:
                    stream.write(b"unrelated concurrent file")
                raise FileExistsError(path)
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", race_winner), self.assertRaises(FileExistsError):
            self.authoring.open_copy("source/input.fla", "out/race.fla")
        self.assertEqual(target.read_bytes(), b"unrelated concurrent file")

    def test_open_copy_rechecks_source_hash_after_native_dispatch(self):
        source = self.root / "source/input.fla"
        source.write_bytes(b"source FLA")
        original_dispatch = self.authoring.dispatcher

        def mutate_source(request, state):
            result = original_dispatch(request, state)
            source.write_bytes(b"changed while opening")
            return result

        self.authoring.dispatcher = mutate_source
        with self.assertRaises(AnimateUnknown):
            self.authoring.open_copy("source/input.fla", "out/copy.fla")
        packet = json.loads((self.root / ".animate-authoring/pending.json").read_text())
        self.assertEqual(packet["state"], "unknown")
        self.assertFalse(list((self.root / ".animate-authoring").glob("[0-9a-f]" * 32 + ".json")))

    def test_create_import_rechecks_source_hash_after_native_dispatch(self):
        original_dispatch = self.authoring.dispatcher

        def mutate_source(request, state):
            result = original_dispatch(request, state)
            self.source.write_bytes(b"changed during import")
            return result

        self.authoring.dispatcher = mutate_source
        with self.assertRaises(AnimateUnknown):
            self.authoring.create_from_png("source/walk.png", "out/changed.fla")
        packet = json.loads((self.root / ".animate-authoring/pending.json").read_text())
        self.assertEqual(packet["state"], "unknown")
        self.assertFalse(list((self.root / ".animate-authoring").glob("[0-9a-f]" * 32 + ".json")))

    def test_create_import_preserves_destination_created_at_native_dispatch(self):
        original_dispatch = self.authoring.dispatcher
        target = self.root / "out/race.fla"

        def race_destination(request, state):
            if request["operation"] == "create_import":
                target.write_bytes(b"unrelated concurrent file")
            return original_dispatch(request, state)

        self.authoring.dispatcher = race_destination
        with self.assertRaises(AnimateUnknown):
            self.authoring.create_from_png("source/walk.png", "out/race.fla")
        self.assertEqual(target.read_bytes(), b"unrelated concurrent file")
        pending = json.loads((self.root / ".animate-authoring/pending.json").read_text())
        self.assertEqual(pending["state"], "unknown")
        self.assertEqual(pending["parameters"]["document_uri"], target.as_uri())
        self.assertEqual(pending["parameters"]["source_uri"], self.source.as_uri())
        self.assertTrue(pending["parameters"]["staging_uri"].endswith("/document.fla"))
        self.assertEqual(next((self.root / ".animate-authoring/staging").glob("*/document.fla")).read_bytes(),
                         b"synthetic editable FLA")

    def test_rejection_after_app_closes_before_native_dispatch_clears_pending(self):
        states = iter([{"running": True, "process": {"pid": 123}},
                       {"running": False, "process": None}])
        self.authoring.status = lambda: next(states)
        self.authoring.dispatcher = lambda *_args: self.fail("native dispatcher must not run")
        with self.assertRaises(AnimateRejected):
            self.authoring._mutate("synthetic", {})
        self.assertFalse((self.root / ".animate-authoring/pending.json").exists())

    def test_unknown_native_result_keeps_journal_and_blocks_retry(self):
        def unknown(_request, _state):
            raise RuntimeError("synthetic timeout")

        self.authoring.dispatcher = unknown
        with self.assertRaises(AnimateUnknown):
            self.authoring.create_from_png("source/walk.png", "out/unknown.fla")
        pending = json.loads((self.root / ".animate-authoring/pending.json").read_text())
        self.assertEqual(pending["state"], "unknown")
        with self.assertRaises(AnimateUnknown):
            self.authoring.create_from_png("source/walk.png", "out/again.fla")

    def test_workspace_symlink_and_existing_output_are_rejected(self):
        (self.root / "link").symlink_to(self.root / "source", target_is_directory=True)
        with self.assertRaises(AnimateRejected):
            self.authoring.create_from_png("link/walk.png", "out/copy.fla")
        with self.assertRaises(AnimateRejected):
            self.authoring.create_from_png("source/walk.png", "out/walk.png")

    def test_injected_dispatch_requires_native_result_envelope(self):
        self.authoring.dispatcher = lambda request, _state: {
            "version": 1, "operation_id": request["operation_id"], "ok": True, "documents": []}
        with self.assertRaises(AnimateUnknown):
            self.authoring.inspect()


class AnimateProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_catalog_is_lazy_typed_and_rejects_invalid_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, data = root / "workspace", root / "data"
            workspace.mkdir()
            data.mkdir()
            parameters = StdioServerParameters(command=sys.executable,
                args=["-m", "cavi_adobe_mcp.animate_server"],
                env={**os.environ, "CAVI_ADOBE_DATA": str(data),
                     "CAVI_ADOBE_WORKSPACE": str(workspace)})
            async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as client:
                initialized = await client.initialize()
                self.assertEqual(initialized.server_info.name, "cavi-animate-authoring")
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                self.assertEqual(set(tools), {"animate_status", "animate_inspect", "animate_open_copy",
                                              "animate_create_from_png",
                                              "animate_set_stage_size_from_source", "animate_export_frames", "animate_edit_timeline", "animate_close_copy", "animate_operation_control"})
                for tool in tools.values():
                    self.assertTrue(tool.output_schema["required"], tool.name)
                self.assertFalse((workspace / ".animate-authoring").exists())

                rejected = await client.call_tool("animate_create_from_png", {
                    "source": "../outside.png", "destination": "output/owned.fla"})
                self.assertTrue(rejected.is_error)
                self.assertEqual(rejected.structured_content["error"]["outcome"], "rejected")
                self.assertFalse((workspace / ".animate-authoring").exists())


class AnimateTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "source").mkdir()
        (self.root / "out").mkdir()
        self.authoring = AnimateAuthoring(self.root)

    def fake_app(self):
        """An Animate bundle with the installed identity, for transport tests that never run it."""
        app = self.root / "Animate.app"
        (app / "Contents/MacOS").mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": APP_ID, "CFBundleExecutable": "Animate"}))
        (app / "Contents/MacOS/Animate").write_text("")
        return app

    @staticmethod
    def local(uri):
        return Path(unquote(urlparse(uri).path))

    def test_fixed_launcher_quotes_file_uris_and_launches_once_through_open_doc(self):
        unusual = self.root / 'source/Artist "Walk" \'s.png'
        Image.new("RGBA", (2, 2), (1, 2, 3, 255)).save(unusual)
        request = {"version": 1, "operation": "create_import", "operation_id": "a" * 32,
                   "source_uri": unusual.as_uri(), "document_uri": (self.root / 'out/Walk "Copy".fla').as_uri(),
                   "expected_process": {"pid": 123}}
        state = {"running": True, "process": {"pid": 123}}
        observed = {}

        def launch(argv, **_kwargs):
            observed["open_calls"] = observed.get("open_calls", 0) + 1
            observed["argv"] = argv
            self.assertEqual(Path(argv[-1]).name, "animate-" + request["operation_id"] + ".jsfl")
            launcher = Path(argv[-1]).read_text()
            literals = re.findall(r'"(?:\\.|[^"\\])*"', launcher)
            driver_uri, entry, request_uri, response_uri, cancel_uri = map(json.loads, literals)
            self.assertEqual(entry, "caviDispatch")
            self.assertEqual(driver_uri, Path(__import__("cavi_adobe_mcp.animate_authoring", fromlist=["DRIVER"]).DRIVER).as_uri())
            request_path = self.local(request_uri)
            observed["request"] = json.loads(request_path.read_text())
            self.assertEqual(observed["request"]["volume_name"], "Macintosh HD")
            self.assertEqual(observed["request"]["source_uri"], unusual.as_uri())
            self.assertEqual(unusual, Path(unquote(urlparse(observed["request"]["source_uri"]).path)))
            response_path = self.local(response_uri)
            response_path.write_text(json.dumps({"version": 1, "operation_id": request["operation_id"],
                                                 "ok": True, "result": {"status": "synthetic"}}))
            self.assertFalse(self.local(cancel_uri).exists())
            return subprocess.CompletedProcess(argv, 0, "", "")

        self.authoring.dispatcher = None
        self.authoring.app_path = self.fake_app()
        self.authoring.store.mkdir(mode=0o700)
        with patch("cavi_adobe_mcp.animate_authoring.sys.platform", "darwin"), \
                patch("cavi_adobe_mcp.animate_authoring.subprocess.run", side_effect=launch), \
                patch.object(self.authoring, "status", return_value=state), \
                patch.object(self.authoring, "_root_volume_name", return_value="Macintosh HD"):
            response = self.authoring._native(request)

        self.assertEqual(response["operation_id"], request["operation_id"])
        self.assertEqual(observed["open_calls"], 1)
        self.assertEqual(observed["argv"][:4], ["/usr/bin/open", "-a", str(self.authoring.app_path), "--"])
        self.assertEqual(observed["argv"][0], "/usr/bin/open")

    def test_exact_jsfl_prompt_handler_runs_only_for_missing_native_receipt(self):
        operation_id = "b" * 32
        state = {"running": True, "process": {"pid": 123, "started": "Tue Nov 3 03:49:52 2026"}}
        request = {"version": 1, "operation": "inspect", "operation_id": operation_id,
                   "expected_process": state["process"]}
        observed = {}

        def launch(argv, **_kwargs):
            launcher_path = Path(argv[-1])
            self.assertEqual(launcher_path.name, f"animate-{operation_id}.jsfl")
            literals = re.findall(r'"(?:\\.|[^"\\])*"', launcher_path.read_text())
            _, _, _, response_uri, _ = map(json.loads, literals)
            observed["response"] = self.local(response_uri)
            return subprocess.CompletedProcess(argv, 0, "", "")

        def handle(process, observed_operation_id, launcher):
            self.assertEqual(process, state["process"])
            self.assertEqual(observed_operation_id, operation_id)
            self.assertEqual(launcher, f"animate-{operation_id}.jsfl")
            observed["calls"] = observed.get("calls", 0) + 1
            observed["response"].write_text(json.dumps({"version": 1, "operation_id": operation_id,
                                                        "ok": True, "result": {"documents": []}}))
            return {"status": "submitted", "operation_id": operation_id, "launcher": launcher}

        self.authoring.dispatcher = None
        self.authoring.app_path = self.fake_app()
        self.authoring.store.mkdir(mode=0o700)
        with patch("cavi_adobe_mcp.animate_authoring.sys.platform", "darwin"), \
                patch("cavi_adobe_mcp.animate_authoring.subprocess.run", side_effect=launch), \
                patch.object(self.authoring, "status", return_value=state), \
                patch.object(self.authoring, "_root_volume_name", return_value="Macintosh HD"), \
                patch.object(self.authoring, "_handle_jsfl_prompt", side_effect=handle):
            response = self.authoring._native(request)

        self.assertEqual(response["operation_id"], operation_id)
        self.assertEqual(observed["calls"], 1)

    def test_native_receipt_is_validated_after_prompt_handler_timeout(self):
        state = {"running": True, "process": {"pid": 123, "started": "fixture"}}
        self.authoring.dispatcher = None
        self.authoring.app_path = self.fake_app()
        self.authoring.store.mkdir(mode=0o700)
        for index, (receipt_id, helper_failure, expected) in enumerate((
            ("match", "timeout", "success"),
            ("foreign", "timeout", "receipt identity"),
            ("missing", "timeout", "timed out"),
            ("match", "authentication", "authentication"),
        )):
            with self.subTest(receipt_id=receipt_id, helper_failure=helper_failure):
                operation_id = str(index) * 32
                response = self.authoring.store / ("dispatch-" + operation_id) / "response.json"

                def handle(*_args, response=response, operation_id=operation_id,
                           receipt_id=receipt_id, helper_failure=helper_failure):
                    if receipt_id != "missing":
                        response.write_text(json.dumps({"version": 1,
                            "operation_id": operation_id if receipt_id == "match" else "f" * 32,
                            "ok": True, "result": {"documents": []}}))
                    if helper_failure == "timeout":
                        try:
                            raise subprocess.TimeoutExpired(["native-helper"], 5)
                        except subprocess.TimeoutExpired as exc:
                            raise AnimateUnknown("chooser handler timed out") from exc
                    raise AnimateUnknown("authentication prompt was refused")

                with patch("cavi_adobe_mcp.animate_authoring.sys.platform", "darwin"), \
                        patch("cavi_adobe_mcp.animate_authoring.subprocess.run",
                              return_value=subprocess.CompletedProcess([], 0, "", "")), \
                        patch.object(self.authoring, "status", return_value=state), \
                        patch.object(self.authoring, "_root_volume_name", return_value="Macintosh HD"), \
                        patch.object(self.authoring, "_handle_jsfl_prompt", side_effect=handle):
                    request = {"version": 1, "operation": "inspect", "operation_id": operation_id}
                    if expected == "success":
                        self.assertEqual(self.authoring._native(request)["result"], {"documents": []})
                    else:
                        with self.assertRaisesRegex(AnimateUnknown, expected):
                            self.authoring._native(request)


if __name__ == "__main__":
    unittest.main()
