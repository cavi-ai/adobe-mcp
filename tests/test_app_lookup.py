import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cavi_mcp_core.contracts import OperationRejected

from cavi_adobe_mcp import adobe_authoring, animate_authoring
from cavi_adobe_mcp.adobe_authoring import AdobeAuthoring
from cavi_adobe_mcp.animate_authoring import AnimateAuthoring, AnimateRejected

OVERRIDES = ("CAVI_PHOTOSHOP_APP", "CAVI_ILLUSTRATOR_APP", "CAVI_ANIMATE_APP")


class AppLookupTests(unittest.TestCase):
    """Each server honors its override and refuses one that names another application."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        (self.root / "workspace").mkdir()
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in OVERRIDES:
            os.environ.pop(name, None)

    def app(self, name, identifier, version="1.0"):
        app = self.root / "apps" / f"{name}.app"
        (app / "Contents/MacOS").mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": identifier, "CFBundleExecutable": name,
            "CFBundleShortVersionString": version}))
        (app / "Contents/MacOS" / name).write_text("")
        return app

    def authoring_status(self, application):
        with patch.object(adobe_authoring.sys, "platform", "darwin"):
            return AdobeAuthoring(self.root / "workspace", application).status()

    def test_photoshop_and_illustrator_use_their_overrides(self):
        for application, env in (("photoshop", "CAVI_PHOTOSHOP_APP"),
                                 ("illustrator", "CAVI_ILLUSTRATOR_APP")):
            with self.subTest(application=application):
                identifier = adobe_authoring.APPS[application][0]
                app = self.app(f"Chosen {application}", identifier, "27.0")
                os.environ[env] = str(app)
                state = self.authoring_status(application)
                self.assertEqual((state["application"], state["version"], state["running"]),
                                 (str(app), "27.0", False))

    def test_an_override_naming_another_application_is_refused(self):
        os.environ["CAVI_PHOTOSHOP_APP"] = str(self.app("Illustrator", "com.adobe.illustrator"))
        with self.assertRaises(OperationRejected) as caught:
            self.authoring_status("photoshop")
        self.assertEqual(caught.exception.error_code, "APP_IDENTITY_MISMATCH")
        self.assertIn("CAVI_PHOTOSHOP_APP", str(caught.exception))

    def test_animate_uses_its_override(self):
        app = self.app("Animate", animate_authoring.APP_ID, "24.0.15")
        os.environ["CAVI_ANIMATE_APP"] = str(app)
        state = AnimateAuthoring(self.root / "workspace").status()
        self.assertEqual((state["application"], state["version"], state["running"]),
                         (str(app), "24.0.15", False))

    def test_animate_refuses_an_override_naming_another_application(self):
        os.environ["CAVI_ANIMATE_APP"] = str(self.app("Photoshop", "com.adobe.Photoshop"))
        with self.assertRaises(AnimateRejected) as caught:
            AnimateAuthoring(self.root / "workspace").status()
        self.assertEqual(caught.exception.error_code, "APP_IDENTITY_MISMATCH")


if __name__ == "__main__":
    unittest.main()
