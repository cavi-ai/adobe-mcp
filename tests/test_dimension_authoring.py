"""Bounded Dimension render protocol checks using owned file fixtures."""

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image

from cavi_adobe_mcp import dimension_server
from cavi_adobe_mcp.dimension_authoring import (
    DimensionAuthoring,
    DimensionBridge,
    DimensionRejected,
    DimensionUnknown,
    digest,
)

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = {"facts": {"pid": 512}, "sha256": "a" * 64}


class FakeBridge:
    def __init__(self):
        self.identity = IDENTITY
        self.output = None
        self.fail = None
        self.mutate_identity = False
        self.mutate_source = False
        self.calls = 0
        self.launch_result = {"status": "launched", "process_identity": IDENTITY,
            "bundle_id": "com.adobe.dimension",
            "bundle_path": "/Applications/Adobe Dimension/Adobe Dimension.app",
            "launches_application": True}

    def call(self, operation):
        if operation == "launch":
            return self.launch_result
        if operation == "status":
            return {"running": True, "process_identity": self.identity,
                    "authentication_or_permission_prompt": False}
        return {"running": True}

    def render_start(self, parameters, expected_process_identity):
        self.calls += 1
        if self.fail:
            raise self.fail
        if expected_process_identity != self.identity:
            raise DimensionRejected("process identity mismatch", "APP_IDENTITY_MISMATCH")
        if self.output:
            image = Image.new("RGB", (3, 2), (22, 83, 147))
            image.save(parameters["directory"] + "/render.png", format="PNG")
        if self.mutate_identity:
            self.identity = {"facts": {"pid": 513}, "sha256": "b" * 64}
        if self.mutate_source:
            Path(parameters["workspace"], "radio.dn").write_bytes(b"changed scene")
        return {"status": "render_requested", "output": parameters["directory"] + "/render.png",
                "captures": ["before.png", "after.png"], "source_identity": "opened_owned_copy"}


class DimensionBridgeTests(unittest.TestCase):
    def test_observation_request_shape_stays_backward_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = DimensionBridge(directory)
            binary = Path(directory) / "native"
            binary.touch()
            packet = {"version": 1, "operation": "inspect", "ok": True,
                      "result": {"running": True, "windows": []}}
            with patch.object(bridge, "_executable", return_value=binary), \
                    patch("cavi_adobe_mcp.dimension_authoring.subprocess.run", return_value=__import__("subprocess").CompletedProcess(
                        [str(binary)], 0, json.dumps(packet), "")) as run:
                self.assertEqual(bridge.call("inspect"), packet["result"])
            self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"operation": "inspect"})

    def test_launch_uses_fixed_v1_operation_and_validates_the_installed_process(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = DimensionBridge(directory)
            binary = Path(directory) / "native"
            binary.touch()
            packet = {"version": 1, "operation": "launch", "ok": True,
                      "result": {"status": "already_running", "process_identity": IDENTITY,
                          "bundle_id": "com.adobe.dimension",
                          "bundle_path": "/Applications/Adobe Dimension/Adobe Dimension.app",
                          "launches_application": False}}
            with patch.object(bridge, "_executable", return_value=binary), \
                    patch("cavi_adobe_mcp.dimension_authoring.subprocess.run", return_value=__import__("subprocess").CompletedProcess(
                        [str(binary)], 0, json.dumps(packet), "")) as run:
                self.assertEqual(bridge.call("launch"), packet["result"])
            self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"version": 1, "operation": "launch"})
            packet["result"]["bundle_path"] = "/Applications/Other.app"
            with patch.object(bridge, "_executable", return_value=binary), \
                    patch("cavi_adobe_mcp.dimension_authoring.subprocess.run", return_value=__import__("subprocess").CompletedProcess(
                        [str(binary)], 0, json.dumps(packet), "")), \
                    self.assertRaises(DimensionUnknown):
                bridge.call("launch")

    def test_render_request_carries_expected_identity_and_native_errors_preserve_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = DimensionBridge(directory)
            binary = Path(directory) / "native"
            binary.touch()
            packet = {"version": 1, "operation": "render_start", "ok": False,
                      "error": {"code": "APP_IDENTITY_MISMATCH", "message": "wrong process",
                                "outcome": "rejected", "retry_safe": False}}
            request = {"workspace": directory, "source": directory + "/render.dn",
                       "directory": directory, "basename": "render"}
            with patch.object(bridge, "_executable", return_value=binary), \
                    patch("cavi_adobe_mcp.dimension_authoring.subprocess.run", return_value=__import__("subprocess").CompletedProcess(
                        [str(binary)], 0, json.dumps(packet), "")) as run, \
                    self.assertRaises(DimensionRejected):
                bridge.render_start(request, IDENTITY)
            sent = json.loads(run.call_args.kwargs["input"])
            self.assertEqual(sent["parameters"], request)
            self.assertEqual(sent["expected_process_identity"], IDENTITY)

    def test_unknown_native_error_retains_phase_dispatch_and_exact_code(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = DimensionBridge(directory)
            binary = Path(directory) / "native"
            binary.touch()
            packet = {"version": 1, "operation": "render_start", "ok": False,
                      "error": {"code": "NATIVE_WORKFLOW_FAILURE", "message": "window unavailable",
                                "outcome": "unknown", "retry_safe": False,
                                "phase": "wait_for_document_view", "render_dispatched": True}}
            with patch.object(bridge, "_executable", return_value=binary), \
                    patch("cavi_adobe_mcp.dimension_authoring.subprocess.run", return_value=__import__("subprocess").CompletedProcess(
                        [str(binary)], 0, json.dumps(packet), "")), \
                    self.assertRaises(DimensionUnknown) as caught:
                bridge.render_start({"workspace": directory}, IDENTITY)
            self.assertEqual(caught.exception.error_code, "NATIVE_WORKFLOW_FAILURE")
            self.assertIn("phase=wait_for_document_view", str(caught.exception))
            self.assertIn("render_dispatched=true", str(caught.exception))


class DimensionRenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "renders").mkdir()
        (self.root / "radio.dn").write_bytes(b"owned scene bytes")
        self.authoring = DimensionAuthoring(self.root)
        self.bridge = FakeBridge()
        self.authoring.bridge = self.bridge

    def start(self, name="renders/job"):
        return self.authoring.render_start("radio.dn", name)

    def test_launch_accepts_only_fixed_app_receipt(self):
        self.assertEqual(self.authoring.launch()["status"], "launched")

    def test_submission_stays_pending_then_builds_verified_opaque_rgba_handoff(self):
        started = self.start()
        self.assertEqual(started["status"], "render_requested")
        self.assertEqual(Path(started["owned_copy"]).name, "render.dn")
        self.assertEqual(Path(started["owned_copy"]).read_bytes(), (self.root / "radio.dn").read_bytes())
        self.assertEqual(self.authoring.render_inspect(started["operation_id"])["status"], "pending")
        self.bridge.output = True
        # The fake writes to the path only when called; write the pending render now.
        Image.new("RGB", (3, 2), (22, 83, 147)).save(
            started["output"], format="PNG", icc_profile=b"test-profile-bytes")
        receipt = self.authoring.render_inspect(started["operation_id"])
        self.assertEqual(receipt["status"], "complete")
        self.assertEqual(receipt["mode"], "RGB")
        self.assertEqual(receipt["bit_depth"], 8)
        self.assertTrue(receipt["handoff_converted"])
        self.assertEqual(digest(Path(receipt["output"])), receipt["sha256"])
        with Image.open(receipt["output"]) as rgb, Image.open(receipt["handoff_output"]) as rgba:
            self.assertEqual(rgba.mode, "RGBA")
            self.assertEqual(rgba.getchannel("A").getextrema(), (255, 255))
            self.assertEqual(rgb.tobytes(), rgba.convert("RGB").tobytes())
        self.assertEqual(self.authoring.render_inspect(started["operation_id"]), receipt)
        self.assertFalse((self.authoring.store / "active.json").exists())

    def test_rgba_render_is_passed_through_without_rewriting_native_render(self):
        started = self.start()
        Image.new("RGBA", (2, 2), (1, 2, 3, 4)).save(started["output"], format="PNG")
        original_hash = digest(Path(started["output"]))
        receipt = self.authoring.render_inspect(started["operation_id"])
        self.assertFalse(receipt["handoff_converted"])
        self.assertEqual(receipt["output"], receipt["handoff_output"])
        self.assertEqual(receipt["sha256"], original_hash)

    def _assert_existing_receipt_is_preserved(self, mode):
        started = self.start()
        receipt_path = Path(started["output"]).parent / "receipt.json"
        sentinel = b'{"owner":"another process"}\n'
        receipt_path.write_bytes(sentinel)
        image = Image.new(mode, (2, 2), 3 if mode == "P" else (1, 2, 3))
        image.save(started["output"], format="PNG")
        with self.assertRaises(DimensionUnknown):
            self.authoring.render_inspect(started["operation_id"])
        self.assertEqual(receipt_path.read_bytes(), sentinel)
        self.assertTrue((self.authoring.store / "active.json").exists())

    def test_receipt_created_while_rgb_render_pending_is_never_overwritten(self):
        self._assert_existing_receipt_is_preserved("RGB")

    def test_receipt_created_while_unsupported_render_pending_is_never_overwritten(self):
        self._assert_existing_receipt_is_preserved("P")

    def test_unknown_submission_keeps_active_job_and_blocks_replay(self):
        self.bridge.fail = DimensionUnknown("adapter timed out")
        with self.assertRaises(DimensionUnknown) as caught:
            self.start()
        match = re.search(r"operation_id=([0-9a-f]{32})", str(caught.exception))
        self.assertIsNotNone(match)
        self.assertTrue((self.authoring.store / "active.json").exists())
        with self.assertRaises(DimensionUnknown) as second:
            self.start("renders/second")
        self.assertIn(match.group(1), str(second.exception))
        self.assertIn("dimension_render_inspect", str(second.exception))
        self.assertEqual(self.bridge.calls, 1)

    def test_dangling_active_journal_symlink_blocks_new_render(self):
        self.authoring._store()
        active = self.authoring.store / "active.json"
        active.symlink_to(self.root / "missing-active-record")
        with self.assertRaises(DimensionUnknown):
            self.start()
        self.assertTrue(active.is_symlink())
        self.assertEqual(self.bridge.calls, 0)

    def test_process_or_source_change_after_dispatch_remains_unknown(self):
        self.bridge.mutate_identity = True
        with self.assertRaises(DimensionUnknown):
            self.start()
        self.assertTrue((self.authoring.store / "active.json").exists())
        self.assertEqual(self.bridge.calls, 1)

    def test_scene_change_after_dispatch_remains_unknown(self):
        self.bridge.mutate_source = True
        with self.assertRaises(DimensionUnknown):
            self.start()
        self.assertTrue((self.authoring.store / "active.json").exists())

    def test_existing_output_and_path_traversal_are_rejected_without_overwrite(self):
        target = self.root / "renders/existing"
        target.mkdir()
        (target / "keep.txt").write_text("keep")
        with self.assertRaises(DimensionRejected):
            self.authoring.render_start("radio.dn", "renders/existing")
        with self.assertRaises(DimensionRejected):
            self.authoring.render_start("../radio.dn", "renders/unsafe")
        self.assertEqual((target / "keep.txt").read_text(), "keep")
        self.assertEqual(self.bridge.calls, 0)

    def test_directory_appearance_at_mkdir_race_is_preserved(self):
        target = self.root / "renders/raced"
        original_mkdir = Path.mkdir

        def raced(path, *args, **kwargs):
            if path == target:
                original_mkdir(path)
                (path / "keep.txt").write_text("keep")
                raise FileExistsError(path)
            return original_mkdir(path, *args, **kwargs)

        with patch.object(Path, "mkdir", raced), self.assertRaises(DimensionRejected):
            self.authoring.render_start("radio.dn", "renders/raced")
        self.assertEqual((target / "keep.txt").read_text(), "keep")
        self.assertEqual(self.bridge.calls, 0)


class DimensionProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_tool_error_names_journal_id_and_prevents_second_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            (workspace / "renders").mkdir()
            (workspace / "radio.dn").write_bytes(b"owned scene bytes")
            authoring = DimensionAuthoring(workspace)
            bridge = FakeBridge()
            bridge.fail = DimensionUnknown("native helper outcome is unknown", "NATIVE_TIMEOUT")
            authoring.bridge = bridge
            with patch.object(dimension_server, "host", return_value=authoring):
                with self.assertRaises(ToolError) as first:
                    await dimension_server.dimension_render_start("radio.dn", "renders/job")
                operation = re.search(r"operation_id=([0-9a-f]{32})", str(first.exception))
                self.assertIsNotNone(operation)
                self.assertIn("dimension_render_inspect", str(first.exception))
                with self.assertRaises(ToolError) as second:
                    await dimension_server.dimension_render_start("radio.dn", "renders/second")
            self.assertIn(operation.group(1), str(second.exception))
            self.assertEqual(bridge.calls, 1)

    async def test_stdio_catalog_is_lazy_and_typed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, data = root / "workspace", root / "data"
            workspace.mkdir()
            data.mkdir()
            parameters = StdioServerParameters(command=sys.executable,
                args=["-m", "cavi_adobe_mcp.dimension_server"],
                env={**os.environ, "CAVI_ADOBE_DATA": str(data),
                     "CAVI_ADOBE_WORKSPACE": str(workspace)})
            async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as client:
                initialized = await client.initialize()
                self.assertEqual(initialized.server_info.name, "cavi-adobe-dimension")
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                self.assertEqual(set(tools), {"dimension_status", "dimension_inspect", "dimension_launch",
                    "dimension_render_start", "dimension_render_inspect"})
                for tool in tools.values():
                    self.assertTrue(tool.output_schema["required"], tool.name)
                self.assertEqual(tools["dimension_render_start"].input_schema["required"],
                                 ["scene", "directory"])
                self.assertEqual(tools["dimension_render_inspect"].input_schema["required"], ["operation_id"])
                self.assertFalse(tools["dimension_launch"].annotations.model_dump(by_alias=True)["readOnlyHint"])
                self.assertEqual(tools["dimension_launch"].input_schema.get("required", []), [])
                inspect_annotations = tools["dimension_render_inspect"].annotations.model_dump(by_alias=True)
                self.assertFalse(inspect_annotations["readOnlyHint"])
                self.assertFalse(inspect_annotations["destructiveHint"])
                self.assertTrue(inspect_annotations["idempotentHint"])
                self.assertFalse(inspect_annotations["openWorldHint"])
                self.assertFalse((workspace / ".dimension-authoring").exists())
                rejected = await client.call_tool("dimension_render_start", {
                    "scene": "../outside.dn", "directory": "renders/job"})
                self.assertTrue(rejected.is_error)
                self.assertFalse((workspace / ".dimension-authoring").exists())


if __name__ == "__main__":
    unittest.main()
