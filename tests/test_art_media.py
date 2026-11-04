"""Real file/protocol tests; generated fixtures are not native Adobe proof."""

import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

from cavi_adobe_mcp import art_media as media
from cavi_adobe_mcp import authoring_server, native_media

ROOT = Path(__file__).resolve().parents[1]
AUTHORING_TOOLS = {
    "adobe_status", "adobe_inspect", "adobe_open_copy", "adobe_create_canvas", "adobe_edit_layers",
    "adobe_add_group", "adobe_place_image", "adobe_add_rectangle", "adobe_add_path", "adobe_add_text",
    "adobe_apply_mask", "adobe_fonts", "adobe_export", "adobe_close_copy", "adobe_operation_control",
    "after_effects_render", "media_review_movie",
}


def fixture(root):
    for index, color in enumerate(((240, 10, 0, 123), (0, 220, 90, 0))):
        image = Image.new("RGBA", (4, 3), color)
        image.save(root / f"part{index}.png")
        image.close()
    spec = {"version": 1, "name": "Walk", "fps": {"numerator": 24000, "denominator": 1001},
            "alpha": "straight", "loop": True, "sources": [],
            "frames": [{"name": f"Pose{i}", "path": f"part{i}.png"} for i in (1, 0)]}
    media.write(root / "recipe.json", spec)
    return spec


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.spec = fixture(self.root)

    def test_atlas_preserves_rgba_order_and_provenance(self):
        result = media.ingest(self.root, "recipe.json", "ingest")
        packed = media.pack(self.root, result["receipt"], "packed", 2, 1)
        data = media.load(Path(packed["receipt"]))
        self.assertEqual(data["fps"], self.spec["fps"])
        self.assertEqual([f["name"] for f in data["frames"]], ["Pose1", "Pose0"])
        for old, new in zip(self.spec["frames"], data["frames"], strict=True):
            with Image.open(self.root / old["path"]) as source, media._crop(self.root, new) as crop:
                self.assertEqual(source.tobytes(), crop.tobytes())
        (self.root / "part0.png").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Source changed"):
            media.validate(self.root, packed["receipt"])

    def test_rejects_changed_inputs_escapes_duplicates_and_bad_rates(self):
        for key, value in (("fps", {"numerator": True, "denominator": 1}),
                           ("alpha", "premultiplied"), ("frames", [self.spec["frames"][0]] * 2)):
            with self.subTest(key=key):
                (self.root / "recipe.json").write_text(json.dumps({**self.spec, key: value}))
                with self.assertRaises(ValueError):
                    media.ingest(self.root, "recipe.json", "unused")
                self.assertFalse((self.root / "unused").exists())
        with self.assertRaises(ValueError):
            media.inside(self.root, "../outside")
        (self.root / "recipe.json").write_text(json.dumps(self.spec))
        result = media.ingest(self.root, "recipe.json", "ingest")
        with self.assertRaises(FileExistsError):
            media.ingest(self.root, "recipe.json", "ingest")
        Image.new("RGBA", (4, 3), "blue").save(self.root / "part0.png")
        with self.assertRaisesRegex(ValueError, "changed"):
            media.validate(self.root, result["receipt"])

    def test_editorial_uses_ordered_png_clips_and_ntsc_rate(self):
        result = media.ingest(self.root, "recipe.json", "ingest")
        editorial = media.editorial(self.root, result["receipt"], "edit")
        root = ET.parse(self.root / "edit/sequence.xml").getroot()
        self.assertEqual(root.tag, "xmeml")
        self.assertEqual(root.findtext("sequence/rate/timebase"), "24")
        self.assertEqual(root.findtext("sequence/rate/ntsc"), "TRUE")
        clips = root.findall("sequence/media/video/track/clipitem")
        self.assertEqual([x.findtext("name") for x in clips], ["Pose1", "Pose0"])
        self.assertEqual([x.findtext("start") for x in clips], ["0", "1"])
        self.assertEqual(editorial["native_import"], "unverified")
        self.assertEqual(len(editorial["outputs"]), 3)

    def test_owned_adobe_inspection_matches_output_contract(self):
        snapshot = {"runtime": {"running": True}, "pending_operation": None, "version": "test"}
        with patch.object(authoring_server, "app") as host:
            host.return_value.inspect_session.return_value = {"session": {"session_id": "test"}, "native": snapshot}
            result = authoring_server.adobe_inspect("photoshop", "test")
        parsed = authoring_server.AdobeSnapshot.model_validate(result)
        self.assertEqual(parsed.runtime, snapshot["runtime"])
        self.assertEqual(result["session"], {"session_id": "test"})

    def test_repeated_frames_reject_expansion_before_export_directory(self):
        self.spec["frames"] = [{"name": f"Held{i}", "path": "part0.png"} for i in range(10)]
        (self.root / "recipe.json").write_text(json.dumps(self.spec))
        result = media.ingest(self.root, "recipe.json", "ingest")
        with patch.object(media, "MAX_EXPANDED_PIXELS", 100):
            for action in (media.editorial, native_media.review_movie):
                with self.subTest(action=action.__name__), self.assertRaisesRegex(ValueError, "expanded RGBA"):
                    action(self.root, result["receipt"], "oversized")
                self.assertFalse((self.root / "oversized").exists())

    def test_native_process_failure_retains_unknown_execution(self):
        directory = self.root / "process"
        directory.mkdir()
        with self.assertRaises(native_media.MediaOutcomeUnknown):
            native_media.run([sys.executable, "-c", "raise SystemExit(3)"], directory)
        execution = media.load(directory / "execution.json")
        self.assertEqual(execution["state"], "unknown")
        self.assertEqual(execution["exit_code"], 3)
        self.assertFalse(execution["retry_safe"])


class MediaProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_schemas_handoff_and_structured_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            env = {**os.environ, "CAVI_ADOBE_DATA": str(root / "data"),
                   "CAVI_ADOBE_WORKSPACE": str(root)}
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "cavi_adobe_mcp.authoring_server"], env=env)
            async with stdio_client(params) as (r, w), ClientSession(r, w) as client:
                await client.initialize()
                catalog = {x.name: x for x in (await client.list_tools()).tools}
                self.assertEqual(set(catalog), AUTHORING_TOOLS)
                for tool in catalog.values():
                    self.assertTrue(tool.output_schema["required"], tool.name)
                bad = await client.call_tool("adobe_open_copy", {"application": "unknown", "source": "x", "destination": "y"})
                self.assertTrue(bad.is_error)
                self.assertEqual(bad.structured_content["error"]["outcome"], "rejected")
