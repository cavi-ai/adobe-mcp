"""Discovery contracts used by the disconnected evaluation fixtures."""

import json
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


GENERATIVE_TOOLS = ("adobe_generative_fill", "adobe_remove_background", "adobe_generative_upscale")


class GenerativeToolContractTests(unittest.IsolatedAsyncioTestCase):
    def manifest_probes(self):
        manifest = json.loads((ROOT / "evals/authoring.inspection.manifest.json").read_text())
        return {probe["tool"]: probe for probe in manifest["probes"]
                if probe.get("tool") in GENERATIVE_TOOLS}

    async def test_catalog_lists_generative_tools_within_the_discovery_budget(self):
        budget = json.loads((ROOT / "evals/authoring.manifest.json").read_text())["probes"][0]
        parameters = StdioServerParameters(command=sys.executable, args=[
            str(ROOT / "evals/simulated_server.py"), "authoring"])
        async with stdio_client(parameters) as (reader, writer), \
                ClientSession(reader, writer) as client:
            await client.initialize()
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        self.assertLessEqual(len(tools), budget["max_tools"])
        for name in GENERATIVE_TOOLS:
            with self.subTest(tool=name):
                self.assertIn(name, tools)
                self.assertFalse(tools[name].annotations.read_only_hint)
                self.assertEqual(tools[name].input_schema["properties"]["application"]["const"],
                                 "photoshop")
        for name in ("adobe_generative_fill", "adobe_generative_upscale"):
            with self.subTest(tool=name):
                self.assertTrue(tools[name].annotations.open_world_hint)
                self.assertTrue(tools[name].annotations.destructive_hint)

    async def test_manifest_probes_each_generative_tool_with_a_rejected_input(self):
        probes = self.manifest_probes()
        self.assertEqual(sorted(probes), sorted(GENERATIVE_TOOLS))
        parameters = StdioServerParameters(command=sys.executable, args=[
            str(ROOT / "evals/simulated_server.py"), "authoring"])
        async with stdio_client(parameters) as (reader, writer), \
                ClientSession(reader, writer) as client:
            await client.initialize()
            for name, probe in probes.items():
                with self.subTest(tool=name):
                    self.assertEqual(probe["expect"], {"outcome": "error"})
                    rejected = await client.call_tool(name, probe["arguments"])
                    self.assertTrue(rejected.is_error)
                    self.assertEqual(rejected.structured_content["error"]["code"],
                                     "INVALID_ARGUMENT")
                    self.assertEqual(rejected.structured_content["error"]["outcome"], "rejected")
            status = await client.call_tool("adobe_status", {"application": "photoshop"})
            self.assertFalse(status.is_error)

    async def test_overlong_generative_fill_prompt_is_rejected(self):
        parameters = StdioServerParameters(command=sys.executable, args=[
            str(ROOT / "evals/simulated_server.py"), "authoring"])
        async with stdio_client(parameters) as (reader, writer), \
                ClientSession(reader, writer) as client:
            await client.initialize()
            rejected = await client.call_tool("adobe_generative_fill", {
                "application": "photoshop", "session_id": "unused", "prompt": "x" * 1001})
            self.assertTrue(rejected.is_error)
            self.assertEqual(rejected.structured_content["error"]["code"], "INVALID_ARGUMENT")
