"""Helper builds run a real compiler: a stand-in script, or swiftc for the shipped sources."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from cavi_adobe_mcp import native

PACKAGE = Path(native.__file__).parent
# Stands in for swiftc: copies the source to -o and counts its runs.
FAKE_SWIFTC = """#!/bin/sh
echo run >> "$(dirname "$0")/runs"
sleep 0.2
cp "$2" "$4"
"""


class BuildHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = self.root / "tools"
        self.tools.mkdir()
        compiler = self.tools / "swiftc"
        compiler.write_text(FAKE_SWIFTC)
        compiler.chmod(0o755)
        self.source = self.root / "helper.swift"
        self.source.write_text("print(1)\n")
        path = patch.dict(os.environ, {"PATH": f"{self.tools}{os.pathsep}{os.environ['PATH']}"})
        path.start()
        self.addCleanup(path.stop)

    def runs(self):
        runs = self.tools / "runs"
        return len(runs.read_text().splitlines()) if runs.exists() else 0

    def test_builds_once_and_reuses_the_pinned_build(self):
        first = native.build_helper(self.source, self.root / "state", "helper")
        second = native.build_helper(self.source, self.root / "state", "helper")
        self.assertEqual(first, second)
        self.assertEqual(first.parent.name, native.sha256(self.source))
        self.assertEqual(json.loads((first.parent / "build.json").read_text()),
                         {"source_sha256": native.sha256(self.source), "sha256": native.sha256(first)})
        self.assertTrue(os.access(first, os.X_OK))
        self.assertEqual(self.runs(), 1)

    def test_a_changed_source_builds_beside_the_old_one(self):
        first = native.build_helper(self.source, self.root / "state", "helper")
        self.source.write_text("print(2)\n")
        second = native.build_helper(self.source, self.root / "state", "helper")
        self.assertNotEqual(first.parent, second.parent)
        self.assertTrue(first.exists() and second.exists())

    def test_concurrent_builds_compile_once(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(
            native.build_helper(self.source, self.root / "state", "helper"))) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(self.runs(), 1)

    def test_a_tampered_cache_is_refused_not_replaced(self):
        helper = native.build_helper(self.source, self.root / "state", "helper")
        helper.write_text("tampered")
        with self.assertRaises(native.HelperError) as caught:
            native.build_helper(self.source, self.root / "state", "helper")
        self.assertEqual(caught.exception.code, "NATIVE_HELPER_UNSAFE")
        self.assertEqual(helper.read_text(), "tampered")
        (helper.parent / "build.json").unlink()
        with self.assertRaises(native.HelperError):
            native.build_helper(self.source, self.root / "state", "helper")

    def test_a_symbolic_link_cache_is_refused(self):
        target = self.root / "elsewhere"
        target.mkdir()
        (self.root / "state").mkdir()
        os.symlink(target, self.root / "state" / "native")
        with self.assertRaises(native.HelperError) as caught:
            native.build_helper(self.source, self.root / "state", "helper")
        self.assertEqual(caught.exception.code, "NATIVE_HELPER_UNSAFE")

    def test_compiler_failures_leave_no_build(self):
        (self.tools / "swiftc").write_text("#!/bin/sh\necho 'error: bad source' >&2\nexit 1\n")
        with self.assertRaises(native.HelperError) as caught:
            native.build_helper(self.source, self.root / "state", "helper")
        self.assertEqual(caught.exception.code, "NATIVE_HELPER_BUILD_FAILED")
        self.assertIn("bad source", str(caught.exception))
        build = self.root / "state/native" / native.sha256(self.source)
        self.assertEqual(sorted(p.name for p in build.iterdir()), ["build.lock"])

    def test_compiler_timeouts_leave_no_build_and_allow_retry(self):
        compiler = self.tools / "swiftc"
        compiler.write_text('#!/bin/sh\nprintf partial > "$4"\nexec sleep 30\n')
        with (patch.object(native, "BUILD_TIMEOUT", 0.2),
              self.assertRaises(native.HelperError) as caught):
            native.build_helper(self.source, self.root / "state", "helper")
        self.assertEqual(caught.exception.code, "NATIVE_HELPER_BUILD_FAILED")
        self.assertIsInstance(caught.exception.__cause__, subprocess.TimeoutExpired)
        build = self.root / "state/native" / native.sha256(self.source)
        self.assertEqual(sorted(p.name for p in build.iterdir()), ["build.lock"])

        compiler.write_text(FAKE_SWIFTC)
        helper = native.build_helper(self.source, self.root / "state", "helper")
        self.assertEqual(helper.read_text(), self.source.read_text())
        self.assertTrue((build / "build.json").is_file())

    def test_a_missing_compiler_or_source_is_unavailable(self):
        with (patch.dict(os.environ, {"PATH": str(self.root / "empty")}),
              self.assertRaises(native.HelperError) as caught):
            native.build_helper(self.source, self.root / "state", "helper")
        self.assertEqual(caught.exception.code, "NATIVE_HELPER_UNAVAILABLE")
        with self.assertRaises(native.HelperError) as caught:
            native.build_helper(self.root / "missing.swift", self.root / "state", "helper")
        self.assertEqual(caught.exception.code, "NATIVE_HELPER_UNAVAILABLE")


# The helpers link macOS frameworks; a Linux swiftc cannot build them.
@unittest.skipUnless(sys.platform == "darwin" and shutil.which("swiftc", path="/usr/bin:/usr/local/bin"),
                     "needs macOS and swiftc")
class ShippedHelperTests(unittest.TestCase):
    def test_every_shipped_helper_compiles(self):
        for source in sorted(PACKAGE.glob("*_native.swift")):
            with self.subTest(source=source.name), tempfile.TemporaryDirectory() as state:
                helper = native.build_helper(source, Path(state), source.stem)
                self.assertTrue(os.access(helper, os.X_OK))
                # Each helper answers an empty request with a JSON refusal, never a crash.
                result = subprocess.run([str(helper)], input="{}", capture_output=True,
                                        text=True, timeout=30, check=False)
                self.assertIn(result.returncode, (0, 1), result.stderr)
                self.assertIsInstance(json.loads(result.stdout or "{}"), dict)


if __name__ == "__main__":
    unittest.main()
