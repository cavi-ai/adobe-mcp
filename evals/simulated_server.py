"""Production MCP tools with synthetic native adapters; never opens Adobe apps.

Only status and inspection native operations are supported. All other native
operations fail closed. File/session validation stays in the production hosts.
"""

import argparse
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from cavi_adobe_mcp import (
    adobe_authoring,
    animate_authoring,
    animate_server,
    authoring_server,
    character_animator_server,
    character_bridge,
    dimension_authoring,
    dimension_server,
)
from cavi_adobe_mcp.character_animator import CharacterAnimator


class SimulatedAdobe(adobe_authoring.AdobeAuthoring):
    def status(self):
        return {"app": self.app, "application": "/simulated/Adobe.app", "version": "simulated",
                "running": True, "process": {"pid": 123, "started": "simulated"},
                "launches_application": False, "authenticates": False}

    @contextmanager
    def _locked(self):
        # Avoid the process-global real-app lock while retaining production read logic.
        yield self.workspace / "pending.json"

    def _native(self, request, state, directory):
        if request["action"] != "inspect":
            raise adobe_authoring.AdobeCommandRejected("Unsupported simulated native operation")
        return {"documents": []}


class SimulatedAnimate(animate_authoring.AnimateAuthoring):
    def __init__(self, workspace):
        super().__init__(workspace, dispatcher=self.dispatch)

    def status(self):
        return {"application": "/simulated/Animate.app", "version": "simulated", "running": True,
                "process": {"pid": 124}, "launches_application": False}

    @staticmethod
    def dispatch(request, state):
        if request["operation"] != "inspect":
            raise animate_authoring.AnimateRejected("Unsupported simulated native operation")
        return {"version": 1, "operation_id": request["operation_id"], "ok": True,
                "result": {"documents": []}}


class SimulatedCharacterBridge:
    def __init__(self, directory=None):
        pass

    def call(self, operation, parameters=None, expected=None, cancelled=None):
        if operation not in {"status", "inspect"}:
            raise character_bridge.CharacterRejected("Unsupported simulated native operation")
        return {"version": 1, "ok": True, "operation": operation,
                "state": {"bundle_id": character_bridge.BUNDLE_ID,
                          "pid": 125, "launch_time": 1.0, "project_roots": [],
                          "fingerprint": "simulated", "windows": [],
                          "accessibility_trusted": True}}


class SimulatedDimensionBridge:
    def call(self, operation):
        if operation not in {"status", "inspect"}:
            raise dimension_authoring.DimensionRejected("Unsupported simulated native operation")
        return {"running": True, "windows": [], "authentication_or_permission_prompt": False,
                "process_identity": {"facts": {"pid": 126}, "sha256": "a" * 64}}

    def render_start(self, parameters, expected_process_identity):
        raise dimension_authoring.DimensionRejected("Unsupported simulated native operation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", choices=("authoring", "animate", "character_animator", "dimension"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="adobe-eval-simulated-") as directory:
        root = Path(directory)
        if args.server == "authoring":
            with patch.object(authoring_server, "app", side_effect=lambda app: SimulatedAdobe(root, app)):
                authoring_server.main()
        elif args.server == "animate":
            host = SimulatedAnimate(root)
            with patch.object(animate_server, "host", return_value=host):
                animate_server.main()
        elif args.server == "character_animator":
            host = CharacterAnimator(root, root / "data", bridge=SimulatedCharacterBridge())
            with (patch.object(character_animator_server, "client", return_value=host),
                  patch.object(character_animator_server, "NativeBridge", SimulatedCharacterBridge),
                  patch.object(character_animator_server, "data_dir", return_value=root / "data")):
                character_animator_server.main()
        else:
            host = dimension_authoring.DimensionAuthoring(root)
            host.bridge = SimulatedDimensionBridge()
            with patch.object(dimension_server, "host", return_value=host):
                dimension_server.main()


if __name__ == "__main__":
    main()
