"""Fault injection checks recovery decisions without editing native applications."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from cavi_mcp_core.journal import fingerprint
from test_adobe_authoring import ContractBridge

from cavi_adobe_mcp.adobe_authoring import AdobeOutcomeUnknown, _native_snapshot


class RecoveryBridge(ContractBridge):
    def _native(self, request, state, directory):
        if request["action"] in ("inspect_recovery", "adopt_recovery"):
            return self.snapshot
        return super()._native(request, state, directory)


class NativeRecoveryTests(unittest.TestCase):
    def test_completed_command_requires_current_copy_and_native_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            host = RecoveryBridge(root)
            (root / "source.png").write_bytes(b"source")
            opened = host.open_copy("source.png", "owned.psd")
            sid = opened["session_id"]
            host.snapshot = _native_snapshot(opened["document"])
            host.error = AdobeOutcomeUnknown("lost response")
            with self.assertRaises(AdobeOutcomeUnknown):
                host.add_group(sid, "Paint")
            pending = json.loads((root / "pending.json").read_text())
            directory = Path(pending["diagnostics"])
            self.addCleanup(lambda: shutil.rmtree(directory, ignore_errors=True))
            operation = pending["operation_id"]
            inspected = host.operation_control("inspect", sid, operation)
            self.assertFalse(inspected["can_reconcile"])
            (directory / "completion.json").write_text(
                json.dumps({"ok": True, "operation_id": operation, "result": host.snapshot})
            )
            inspected = host.operation_control("inspect", sid, operation)
            self.assertTrue(inspected["can_reconcile"])
            with self.assertRaises(ValueError):
                host.operation_control(
                    "adopt_current", sid, operation, "0" * 64, inspected["native_sha256"]
                )
            self.assertTrue((root / "pending.json").exists())
            adopted = host.operation_control(
                "adopt_current",
                sid,
                operation,
                inspected["copy_sha256"],
                inspected["native_sha256"],
            )
            self.assertEqual(adopted["state"], "adopted_current")
            self.assertFalse((root / "pending.json").exists())
            self.assertEqual(host._session(sid)["state"], "owned")
            self.assertEqual(fingerprint(host.snapshot), inspected["native_sha256"])
