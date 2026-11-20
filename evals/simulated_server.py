"""Production MCP tools with synthetic native adapters; never opens Adobe apps.

Only status and inspection native operations are supported. All other native
operations fail closed. File/session validation stays in the production hosts.
"""

import argparse
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import cavi_adobe_mcp.animate.bridge
import cavi_adobe_mcp.animate.server
import cavi_adobe_mcp.core.extendscript
import cavi_adobe_mcp.dimension.bridge
import cavi_adobe_mcp.dimension.server
import cavi_adobe_mcp.illustrator.bridge
import cavi_adobe_mcp.illustrator.server
import cavi_adobe_mcp.photoshop.bridge
import cavi_adobe_mcp.photoshop.server


class SimulatedTransport:
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
            raise cavi_adobe_mcp.core.extendscript.AdobeCommandRejected(
                "Unsupported simulated native operation")
        return {"documents": []}


class SimulatedPhotoshop(SimulatedTransport, cavi_adobe_mcp.photoshop.bridge.PhotoshopAuthoring):
    pass


class SimulatedIllustrator(SimulatedTransport,
                           cavi_adobe_mcp.illustrator.bridge.IllustratorAuthoring):
    pass


class SimulatedAnimate(cavi_adobe_mcp.animate.bridge.AnimateAuthoring):
    def __init__(self, workspace):
        super().__init__(workspace, dispatcher=self.dispatch)

    def status(self):
        return {"application": "/simulated/Animate.app", "version": "simulated", "running": True,
                "process": {"pid": 124}, "launches_application": False}

    @staticmethod
    def dispatch(request, state):
        if request["operation"] != "inspect":
            raise cavi_adobe_mcp.animate.bridge.AnimateRejected(
                "Unsupported simulated native operation")
        return {"version": 1, "operation_id": request["operation_id"], "ok": True,
                "result": {"documents": []}}


class SimulatedDimensionBridge:
    def call(self, operation):
        if operation not in {"status", "inspect"}:
            raise cavi_adobe_mcp.dimension.bridge.DimensionRejected(
                "Unsupported simulated native operation")
        return {"running": True, "windows": [], "authentication_or_permission_prompt": False,
                "process_identity": {"facts": {"pid": 126}, "sha256": "a" * 64}}

    def render_start(self, parameters, expected_process_identity):
        raise cavi_adobe_mcp.dimension.bridge.DimensionRejected(
            "Unsupported simulated native operation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", choices=("photoshop", "illustrator", "animate", "dimension"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="adobe-eval-simulated-") as directory:
        root = Path(directory)
        if args.server == "photoshop":
            with patch.object(cavi_adobe_mcp.photoshop.server, "host",
                              side_effect=lambda timeout=45: SimulatedPhotoshop(root, timeout=timeout)):
                cavi_adobe_mcp.photoshop.server.main()
        elif args.server == "illustrator":
            with patch.object(cavi_adobe_mcp.illustrator.server, "host",
                              side_effect=lambda: SimulatedIllustrator(root)):
                cavi_adobe_mcp.illustrator.server.main()
        elif args.server == "animate":
            host = SimulatedAnimate(root)
            with patch.object(cavi_adobe_mcp.animate.server, "host", return_value=host):
                cavi_adobe_mcp.animate.server.main()
        else:
            host = cavi_adobe_mcp.dimension.bridge.DimensionAuthoring(root)
            host.bridge = SimulatedDimensionBridge()
            with patch.object(cavi_adobe_mcp.dimension.server, "host", return_value=host):
                cavi_adobe_mcp.dimension.server.main()


if __name__ == "__main__":
    main()
