"""Opt in with CAVI_RECOVERY_NATIVE=1; already-running licensed apps required."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import png
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("CAVI_RECOVERY_NATIVE") == "1", "native recovery opt-in")
class NativeRecoveryTests(unittest.TestCase):
    def test_lost_replies_adopt_once_and_reconcile_closed_copies(self):
        asyncio.run(self.run_native())

    async def run_native(self):
        root = Path(
            tempfile.mkdtemp(prefix="recovery-", dir=os.environ.get("CAVI_NATIVE_EVIDENCE"))
        ).resolve()
        (root / "data").mkdir()
        with (root / "source.png").open("wb") as stream:
            png.Writer(32, 32, greyscale=False, alpha=True, bitdepth=16).write(
                stream, [[1000, 22000, 44000, 65535] * 32 for _ in range(32)]
            )
        (root / "source.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="32" '
            'height="32"><rect width="32" height="32" fill="#235"/></svg>'
        )
        records = []
        for kind in ("adobe", "animate"):
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(ROOT / "tests/native_recovery_server.py"), kind],
                env={
                    **os.environ,
                    "PYTHONPATH": str(ROOT / "tests"),
                    "CAVI_ADOBE_DATA": str(root / "data"),
                    "CAVI_ADOBE_WORKSPACE": str(root),
                },
            )
            async with (
                stdio_client(params) as (r, w),
                ClientSession(r, w, read_timeout_seconds=180) as client,
            ):
                await client.initialize()

                async def call(tool_name, fail=False, **args):
                    reply = await client.call_tool(tool_name, args)
                    self.assertEqual(reply.is_error, fail, str(reply))
                    return reply.structured_content

                for app in ("photoshop", "illustrator") if kind == "adobe" else ("animate",):
                    extra = {"application": app} if kind == "adobe" else {}
                    status = await call(
                        "adobe_status" if kind == "adobe" else "animate_status", **extra
                    )
                    self.assertTrue(status["running"])
                    if kind == "adobe":
                        if app == "photoshop":
                            opened = await call(
                                "adobe_create_canvas", width=32, height=32, destination="copy.psd"
                            )
                            self.assertEqual(
                                opened["document"]["bit_depth"], "BitsPerChannelType.SIXTEEN"
                            )
                        else:
                            opened = await call(
                                "adobe_open_copy",
                                **extra,
                                source="source.svg",
                                destination="copy.ai",
                            )
                        sid = opened["session_id"]
                        await call(
                            "adobe_add_group",
                            **extra,
                            session_id=sid,
                            name="DroppedReply",
                            fail=True,
                        )
                    else:
                        opened = await call(
                            "animate_create_from_png", source="source.png", destination="copy.fla"
                        )
                        sid = opened["session"]
                        await call(
                            "animate_edit_timeline",
                            session_id=sid,
                            batch={"edits": [{"action": "add_layer", "layer": "DroppedReply"}]},
                            fail=True,
                        )

                    async def recover(closed=False, kind=kind, extra=extra, sid=sid, app=app):
                        pending = (await call(kind + "_inspect", **extra))["pending_operation"]
                        args = {**extra, "session_id": sid, "operation_id": pending["operation_id"]}
                        observed = await call(kind + "_operation_control", **args)
                        self.assertTrue(observed["can_reconcile"], str(observed))
                        await call(
                            kind + "_operation_control",
                            **args,
                            action="adopt_current",
                            expected_copy_sha256="0" * 64,
                            expected_native_sha256=observed["native_sha256"],
                            fail=True,
                        )
                        adopted = await call(
                            kind + "_operation_control",
                            **args,
                            action="adopt_current",
                            expected_copy_sha256=observed["copy_sha256"],
                            expected_native_sha256=observed["native_sha256"],
                        )
                        self.assertEqual(adopted["state"], "adopted_current")
                        self.assertEqual(bool(adopted["document"].get("closed")), closed)
                        records.append({"app": app, "closed": closed, **adopted})

                    await recover()
                    if kind == "adobe":
                        continued = await call(
                            "adobe_add_group", **extra, session_id=sid, name="AfterRecovery"
                        )
                    else:
                        continued = await call(
                            "animate_edit_timeline",
                            session_id=sid,
                            batch={"edits": [{"action": "add_layer", "layer": "AfterRecovery"}]},
                        )
                    self.assertEqual(
                        sum(
                            layer["name"] == "DroppedReply"
                            for layer in continued["document"]["layers"]
                        ),
                        1,
                    )
                    if app == "photoshop":
                        await call(
                            "adobe_add_rectangle",
                            **extra,
                            session_id=sid,
                            name="Color",
                            bounds=[0, 0, 16, 16],
                            rgb=[40, 120, 180],
                        )
                        exported = await call(
                            "adobe_export",
                            **extra,
                            session_id=sid,
                            destination="canvas.png",
                            usage="color",
                        )
                        self.assertEqual(exported["artifact"]["bit_depth"], 16)
                    await call(kind + "_close_copy", **extra, session_id=sid, fail=True)
                    await recover(closed=True)
                    self.assertIsNone((await call(kind + "_inspect", **extra))["pending_operation"])
        (root / "proof.json").write_text(json.dumps(records, indent=2))
