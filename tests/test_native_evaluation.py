"""Fail-closed native runner contracts; these unit tests do not run Adobe apps."""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mcp.types import CallToolResult

from evals.run import background_manifest

SPEC = importlib.util.spec_from_file_location(
    "adobe_native_evaluation", Path(__file__).resolve().parents[1] / "evals/native.py")
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


class NativeSelectionTests(unittest.TestCase):
    def test_explicit_native_selection_refuses_other_platforms(self):
        with (patch.object(native.sys, "platform", "linux"),
              contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure):
            native.main(["--workspace", "/unused", "--ae-recipe", "a.json"])
        self.assertEqual(failure.exception.code, 2)

    def test_missing_recipe_refuses_before_any_app_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(native.sys, "platform", "darwin"), \
                    patch.object(native.NativeRun, "check_installed") as called, \
                    contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as failure:
                native.main(["--workspace", str(root), "--ae-recipe", "missing.json"])
            self.assertEqual(failure.exception.code, 2)
            called.assert_not_called()

    def test_ui_tool_in_native_manifest_refuses_before_server_start(self):
        manifest = {"probes": [{"probe": "output-schema", "access": "read_only",
                                "tool": "animate_inspect", "arguments": {}}]}
        with self.assertRaisesRegex(ValueError, "background tool"):
            background_manifest("animate", manifest)


class NativeResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_ui_tools_are_refused_before_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = native.NativeRun(root, root, {})
            client = AsyncMock()
            for tool in ("adobe_inspect", "animate_inspect", "animate_create_from_png", "dimension_launch",
                         "dimension_render_start", "capture", "project_save_copy"):
                with self.subTest(tool=tool), self.assertRaisesRegex(RuntimeError, "background"):
                    await run.call(client, tool)
            client.call_tool.assert_not_called()

    async def test_native_tool_failure_is_recorded_once_and_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = native.NativeRun(root, root, {})
            client = AsyncMock()
            client.call_tool.return_value = CallToolResult(isError=True, content=[],
                structuredContent={"error": {"code": "AUTHENTICATION_BOUNDARY"}})
            with self.assertRaisesRegex(RuntimeError, "Native tool dimension_status failed"):
                await run.call(client, "dimension_status")
            self.assertEqual(client.call_tool.await_count, 1)
            record = json.loads((root / "native-calls.json").read_text())
            self.assertTrue(record[0]["reply"]["isError"])

    async def test_observed_prompt_stops_a_success_shaped_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = native.NativeRun(root, root, {})
            client = AsyncMock()
            client.call_tool.return_value = CallToolResult(content=[],
                structuredContent={"native": {"authentication_or_permission_prompt": True}})
            with self.assertRaisesRegex(RuntimeError, "authentication or permission prompt"):
                await run.call(client, "dimension_status")
            self.assertEqual(client.call_tool.await_count, 1)
