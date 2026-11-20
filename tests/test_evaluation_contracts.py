"""Discovery contracts used by the disconnected evaluation fixtures."""

import sys
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


class SimulatedProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_arguments_are_rejected_at_the_mcp_boundary(self):
        cases = (
            ("authoring", "adobe_status", {"application": "photoshop"}),
            ("animate", "animate_status", {}),
            ("dimension", "dimension_status", {}),
        )
        for server, tool, arguments in cases:
            with self.subTest(server=server):
                parameters = StdioServerParameters(command=sys.executable, args=[
                    str(ROOT / "evals/simulated_server.py"), server])
                async with stdio_client(parameters) as (reader, writer), \
                        ClientSession(reader, writer) as client:
                    await client.initialize()
                    rejected = await client.call_tool(tool, {**arguments, "unknown_setting": True})
                    self.assertTrue(rejected.is_error, "Unknown arguments must not be ignored")
                    error = rejected.structured_content["error"]
                    self.assertEqual(error["code"], "INVALID_ARGUMENT")
                    self.assertEqual(error["outcome"], "rejected")
                    self.assertFalse(error["retry_safe"])
                    accepted = await client.call_tool(tool, arguments)
                    self.assertFalse(accepted.is_error)
                    self.assertFalse(accepted.structured_content["launches_application"])

    async def test_rejected_paths_leave_each_session_usable(self):
        cases = (
            ("authoring", "adobe_open_copy", {"application": "photoshop", "source": "../input.psd",
                                             "destination": "copy.psd"},
             "adobe_status", {"application": "photoshop"}),
            ("animate", "animate_open_copy", {"source": "../input.fla", "destination": "copy.fla"},
             "animate_status", {}),
            ("dimension", "dimension_render_start", {"scene": "../input.dn", "directory": "render"},
             "dimension_status", {}),
        )
        for server, rejected_tool, arguments, status_tool, status_arguments in cases:
            with self.subTest(server=server):
                parameters = StdioServerParameters(command=sys.executable, args=[
                    str(ROOT / "evals/simulated_server.py"), server])
                async with stdio_client(parameters) as (reader, writer), \
                        ClientSession(reader, writer) as client:
                    await client.initialize()
                    rejected = await client.call_tool(rejected_tool, arguments)
                    self.assertTrue(rejected.is_error)
                    error = rejected.structured_content["error"]
                    self.assertEqual(error["code"], "INVALID_ARGUMENT")
                    self.assertEqual(error["outcome"], "rejected")
                    self.assertEqual(error["operation"], rejected_tool)
                    self.assertFalse(error["retry_safe"])
                    self.assertEqual(error["next_action"], "inspect_inputs")
                    status = await client.call_tool(status_tool, status_arguments)
                    self.assertFalse(status.is_error)
                    self.assertFalse(status.structured_content["launches_application"])
