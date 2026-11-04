"""Native bridge request identity serialization tests."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cavi_adobe_mcp.character_bridge import CharacterUnknown, NativeBridge


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

    def test_continue_chooser_request_carries_identity_for_atomic_activation(self):
        expected = {
            "pid": 58131,
            "launch_time": 1793825743.48212,
            "fingerprint": "background-chooser-fingerprint",
            "bundle_id": "com.adobe.Character-Animator.application",
            "application": "/Applications/Adobe Character Animator 2026/Adobe Character Animator 2026.app",
            "project_roots": ["/workspace/owned-project"],
        }
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "native-helper"
            helper.write_bytes(b"fixture helper")
            process_holder = []

            def popen(_args, *, stdout, **_kwargs):
                process = _CompletedProcess(stdout, "continue_chooser")
                process_holder.append(process)
                stdout.write(json.dumps({"version": 1, "operation": "continue_chooser", "ok": True,
                                         "state": {}, "result": {}}).encode())
                stdout.flush()
                return process

            with (patch("cavi_adobe_mcp.character_bridge.application",
                        return_value=(Path(expected["application"]), {})),
                  patch.object(NativeBridge, "executable", return_value=helper),
                  patch("cavi_adobe_mcp.character_bridge.subprocess.Popen", side_effect=popen)):
                NativeBridge(directory).call(
                    "continue_chooser", {"kind": "import", "operation_id": "a" * 32},
                    expected=expected)

        request = json.loads(process_holder[0].stdin.data)
        self.assertEqual(request["expected"], {key: expected[key] for key in (
            "pid", "launch_time", "fingerprint", "bundle_id", "application", "project_roots")})

    def test_continue_chooser_preserves_structured_submission_context(self):
        response = {"version": 1, "operation": "continue_chooser", "ok": False,
                    "error": {"code": "OUTCOME_UNKNOWN", "outcome": "unknown",
                              "phase": "go_to_sheet_readiness", "submission_may_have_started": False,
                              "continuation_diagnostics": {"stage": "go_to_sheet", "last_ready": True,
                                                           "fingerprint_stable": False, "observations": 5},
                              "message": "observed chooser did not settle"}}
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "native-helper"
            helper.write_bytes(b"fixture helper")
            process_holder = []

            def popen(_args, *, stdout, **_kwargs):
                process = _CompletedProcess(stdout, "continue_chooser")
                process_holder.append(process)
                stdout.write(json.dumps(response).encode())
                stdout.flush()
                return process

            with (patch("cavi_adobe_mcp.character_bridge.application",
                        return_value=(Path("/Applications/Adobe Character Animator 2026.app"), {})),
                  patch.object(NativeBridge, "executable", return_value=helper),
                  patch("cavi_adobe_mcp.character_bridge.subprocess.Popen", side_effect=popen),
                  self.assertRaises(CharacterUnknown) as raised):
                NativeBridge(directory).call("continue_chooser", {"kind": "import"})
            context = raised.exception.native_context
            self.assertEqual(context["phase"], "go_to_sheet_readiness")
            self.assertIs(context["submission_may_have_started"], False)
            self.assertEqual(context["continuation_diagnostics"], {
                "stage": "go_to_sheet", "last_ready": True,
                "fingerprint_stable": False, "observations": 5,
            })
            self.assertEqual(len(context["attempt_id"]), 32)
            self.assertEqual(Path(context["progress_path"]).stat().st_mode & 0o777, 0o600)

    def test_interrupted_atomic_attempt_retains_phase_trace(self):
        trace = '{"event":"observe_tree_progress","nodes":64}\n'

        class _InterruptedProcess(_CompletedProcess):
            def __init__(self, stdout, operation):
                super().__init__(stdout, operation)
                self.returncode = None

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = -15

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                self.returncode = -9

        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            runtime.mkdir()
            helper = Path(directory) / "native-helper"
            helper.write_bytes(b"fixture helper")

            def popen(_args, *, stderr, **_kwargs):
                stderr.write(trace.encode())
                stderr.flush()
                return _InterruptedProcess(None, "continue_chooser")

            with (patch("cavi_adobe_mcp.character_bridge.application",
                        return_value=(Path("/Applications/Adobe Character Animator 2026.app"), {})),
                  patch.object(NativeBridge, "executable", return_value=helper),
                  patch("cavi_adobe_mcp.character_bridge.subprocess.Popen", side_effect=popen),
                  self.assertRaises(CharacterUnknown) as raised):
                NativeBridge(runtime, timeout=0.01).call(
                    "continue_chooser", {"operation_id": "a" * 32, "kind": "import"})
            context = raised.exception.native_context
            self.assertTrue(Path(context["progress_path"]).is_file())

        self.assertEqual(context["phase"], "transport_interrupted")
        self.assertIsNone(context["submission_may_have_started"])
        self.assertEqual(context["progress"], [trace.strip()])


if __name__ == "__main__":
    unittest.main()
