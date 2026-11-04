"""Native test transport: drop successful replies after the application writes its receipt."""

import sys

if sys.argv[1] == "adobe":
    from cavi_adobe_mcp import adobe_authoring as adapter
    from cavi_adobe_mcp.authoring_server import mcp

    original = adapter.AdobeAuthoring._native

    def native(self, request, *args, **kwargs):
        result = original(self, request, *args, **kwargs)
        if (
            request.get("action") == "add_group" and request.get("name") == "DroppedReply"
        ) or request.get("action") == "close_copy":
            raise adapter.AdobeOutcomeUnknown("Injected loss after native completion")
        return result

    adapter.AdobeAuthoring._native = native
else:
    from cavi_adobe_mcp import animate_authoring as adapter
    from cavi_adobe_mcp.animate_server import mcp

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
