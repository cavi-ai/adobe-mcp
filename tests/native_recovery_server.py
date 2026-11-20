"""Native test transport: drop successful replies after the application writes its receipt."""

import sys

kind = sys.argv[1]
if kind in ("photoshop", "illustrator"):
    from cavi_adobe_mcp.core.extendscript import AdobeOutcomeUnknown

    if kind == "photoshop":
        from cavi_adobe_mcp.photoshop.bridge import PhotoshopAuthoring as host
        from cavi_adobe_mcp.photoshop.server import mcp
    else:
        from cavi_adobe_mcp.illustrator.bridge import IllustratorAuthoring as host
        from cavi_adobe_mcp.illustrator.server import mcp

    original = host._native

    def native(self, request, *args, **kwargs):
        result = original(self, request, *args, **kwargs)
        if (
            request.get("action") == "add_group" and request.get("name") == "DroppedReply"
        ) or request.get("action") == "close_copy":
            raise AdobeOutcomeUnknown("Injected loss after native completion")
        return result

    host._native = native
else:
    from cavi_adobe_mcp.animate import bridge as adapter
    from cavi_adobe_mcp.animate.server import mcp

    original = adapter.AnimateAuthoring._native

    def native(self, request, *args, **kwargs):
        result = original(self, request, *args, **kwargs)
        if (
            request.get("operation") == "edit_timeline"
            and any(edit.get("layer") == "DroppedReply" for edit in request.get("edits", []))
        ) or request.get("operation") == "close_copy":
            raise adapter.AnimateUnknown("Injected loss after native completion")
        return result

    adapter.AnimateAuthoring._native = native

mcp.run(transport="stdio")
