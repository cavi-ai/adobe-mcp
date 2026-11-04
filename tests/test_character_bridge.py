"""Native bridge request identity serialization tests."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cavi_adobe_mcp.character_bridge import NativeBridge


class _InputCapture:
    def __init__(self):
        self.data = bytearray()

    def write(self, value):
        self.data.extend(value)

    def close(self):
        pass


class _CompletedProcess:
    def __init__(self, stdout, operation):
        self.stdin = _InputCapture()
        self.returncode = 0
        self.stdout = stdout
        self.operation = operation

    def poll(self):
        return self.returncode


class CharacterBridgeRequestTests(unittest.TestCase):
    def test_focus_request_carries_identity_needed_for_scoped_native_activation(self):
        expected = {
            "pid": 14318,
            "launch_time": 1793813160.279797,
            "fingerprint": "native-state-fingerprint",
            "bundle_id": "com.adobe.Character-Animator.application",
            "application": "/Applications/Adobe Character Animator 2026/Adobe Character Animator 2026.app",
            "project_roots": ["/workspace/owned-project"],
            "windows": [{"title": "transient and intentionally omitted"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "native-helper"
            helper.write_bytes(b"fixture helper")
            process_holder = []

            def popen(_args, *, stdout, **_kwargs):
                process = _CompletedProcess(stdout, "focus")
                process_holder.append(process)
                stdout.write(json.dumps({"version": 1, "operation": "focus", "ok": True,
                                         "state": {}, "result": {}}).encode())
                stdout.flush()
                return process

            with (patch("cavi_adobe_mcp.character_bridge.application",
                        return_value=(Path(expected["application"]), {})),
                  patch.object(NativeBridge, "executable", return_value=helper),
                  patch("cavi_adobe_mcp.character_bridge.subprocess.Popen", side_effect=popen)):
                NativeBridge(directory).call(
                    "focus", {"kind": "import", "project_root": expected["project_roots"][0]},
                    expected=expected)

        request = json.loads(process_holder[0].stdin.data)
        self.assertEqual(request["expected"], {key: expected[key] for key in (
            "pid", "launch_time", "fingerprint", "bundle_id", "application", "project_roots")})


if __name__ == "__main__":
    unittest.main()
