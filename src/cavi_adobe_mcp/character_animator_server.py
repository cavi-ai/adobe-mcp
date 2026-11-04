"""Typed Character Animator MCP with cooperative ownership and native evidence."""

import threading
from typing import Annotated, Any, Literal

import anyio.to_thread
from cavi_mcp_core.server import DESTRUCTIVE as MUTATE
from cavi_mcp_core.server import READ_ONLY as READ
from cavi_mcp_core.server import WRITES as WRITE
from cavi_mcp_core.server import Server
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, StrictBool, StrictInt

from . import character_media
from .character_animator import CharacterAnimator
from .character_bridge import CharacterRejected, CharacterUnknown, NativeBridge
from .config import data_dir, workspace
from .contracts import Result

mcp = Server("cavi-character-animator-mcp", package="cavi-adobe-mcp", anticipated=(), instructions=(
    "Controls the installed macOS Character Animator through a bounded native adapter. "
    "Start with character_status and inspect. Claim session_control using the observed project_id. "
    "Save a new owned project copy before scene mutations. Native menus, visual observations and "
    "verified exported files are different evidence; never treat a dispatched command as completion. "
    "Inspect unresolved operation journals instead of automatically retrying. Server startup never "
    "launches the app, requests permissions, records a camera/microphone or signs into an account."
))


class CharacterStatus(Result):
    available: bool
    connected: bool
    native: dict[str, Any] | None
    reason: str | None
    launches_application: Literal[False]


class CharacterInspection(Result):
    project_id: str
    native: dict[str, Any]
    pending_operation: dict[str, Any] | None
    launches_application: Literal[False]


class CharacterOwnership(Result):
    project_id: str
    lease_id: str | None = None
    owner: str | None
    expires_at: float | None
    state: Literal["owned", "unclaimed"]
    pending_operation: dict[str, Any] | None
    prepared_copy_id: str | None = None
    owned_root: str | None = None


class CharacterPreparedCopy(Result):
    status: Literal["prepared_copy"]
    prepared_copy_id: str
    source: str
    destination: str
    source_identity: dict[str, int]
    destination_identity: dict[str, int]
    files: list[dict[str, Any]]
    launches_application: Literal[False]


class CharacterPreparedOpen(Result):
    status: Literal["prepared_copy_opened", "prepared_copy_open_requested"]
    completion: Literal["observed", "unverified"]
    prepared_copy_id: str
    operation_id: str
    project_id: str | None
    native: dict[str, Any]
    retry_safe: Literal[False]
    launches_application: Literal[True]


class CharacterCapture(Result):
    path: str
    sha256: str
    native: dict[str, Any]
    identity: Literal["observed_window_and_ocr"]
    launches_application: Literal[False]


class CharacterProject(Result):
    status: Literal["owned_copy"]
    operation_id: str
    project_id: str
    project_root: str
    project_file: str
    files: list[dict[str, Any]]
    source_identity: Literal["observed_native_project_root"]
    retry_safe: Literal[False]


class CharacterImportedMedia(Result):
    operation_id: str
    status: Literal["native_import_submitted"]
    completion: Literal["unverified"]
    kind: Literal["puppet_psd", "audio_wav"]
    source: dict[str, Any]
    import_copy: dict[str, Any]
    native: dict[str, Any]
    source_identity: Literal["hashed_private_import_copy"]
    retry_safe: Literal[False]


class CharacterSceneAction(Result):
    operation_id: str
    status: Literal["native_scene_action_submitted"]
    action: Literal["add_to_new_scene", "create_take_from_scene_audio", "compute_lip_sync", "export_png_wav"]
    completion: Literal["unverified"]
    expected_state_fingerprint: str
    native_dispatch: dict[str, Any]
    native_after: dict[str, Any]
    selection_identity: Literal["caller_observed_state_fingerprint"]
    retry_safe: Literal[False]


class CharacterProjectSelection(Result):
    operation_id: str
    status: Literal["native_project_item_selected"]
    name: str
    item_type: Literal["puppet", "audio", "scene"]
    extend: bool
    expected_state_fingerprint: str
    selection_evidence: dict[str, Any]
    native: dict[str, Any]
    completion: Literal["unverified"]
    retry_safe: Literal[False]


class CharacterOperation(Result):
    operation_id: str
    status: str
    native: dict[str, Any]
    retry_safe: Literal[False]


class CharacterMediaInspection(Result):
    schema_: Literal["cavi.character-export.v1"] = Field(alias="schema")
    version: Literal[1]
    status: Literal["inspected"]
    name: str
    fps: dict[str, int]
    frame_count: int
    duration: float
    frame_order_source: Literal["explicit_recipe"]
    frame_rate_source: Literal["caller_declared"]
    native_export: Literal["not_verified"]
    launches_applications: Literal[False]


class CharacterHandoff(Result):
    schema_: Literal["cavi.character-handoff.v1"] = Field(alias="schema")
    version: Literal[1]
    status: Literal["ingested"]
    name: str
    media_receipt: dict[str, str]
    cutscene_audio: dict[str, Any] | None
    handoff: dict[str, str]
    native_export: Literal["not_verified"]
    launches_applications: Literal[False]


def client():
    return CharacterAnimator(workspace(), data_dir() / "character-animator")


async def execute(function):
    cancelled = threading.Event()
    try:
        return await anyio.to_thread.run_sync(lambda: function(cancelled), abandon_on_cancel=True)
    except (CharacterRejected, CharacterUnknown, OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    except BaseException:
        cancelled.set()
        raise


@mcp.tool(annotations=READ)
async def character_status() -> CharacterStatus:
    """Inspect app/control availability without launch, recording or permission prompts."""
    def run(cancelled):
        try:
            native = NativeBridge(data_dir() / "character-animator").call("status", cancelled=cancelled)
        except CharacterRejected as exc:
            return {"available": False, "connected": False, "native": None, "reason": str(exc),
                    "launches_application": False}
        state = native["state"]
        connected = type(state.get("pid")) is int and state["pid"] > 0 and state.get("accessibility_trusted") is True
        return {"available": True, "connected": connected, "native": native,
                "reason": None if connected else "Start the app and inspect local control permissions",
                "launches_application": False}
    return await execute(run)


@mcp.tool(annotations=READ)
async def inspect() -> CharacterInspection:
    """Observe actual process, project-root evidence, native controls and unresolved operation state."""
    return await execute(lambda cancelled: client().inspect(cancelled))


@mcp.tool(annotations=WRITE)
async def capture(directory: str) -> CharacterCapture:
    """Capture only the Character Animator window with bounded OCR into a new workspace directory.

    Observed text/geometry are visual evidence, not native scene IDs. Never starts recording.
    """
    return await execute(lambda cancelled: client().capture(directory, cancelled))


@mcp.tool(annotations=WRITE)
async def session_control(action: Literal["inspect", "claim", "renew", "release", "resume",
                                          "open_prepared_copy"],
                          owner: str | None = None, expected_project: str | None = None,
                          lease_id: str | None = None,
                          ttl_seconds: Annotated[StrictInt, Field(ge=30, le=3600)] = 600,
                          pending_operation_id: str | None = None,
                          prepared_copy_id: str | None = None) -> CharacterOwnership | CharacterPreparedOpen:
    """Claim, renew, release, or resume a matching expired lease for one unknown operation.

    Resume requires the expired lease ID, same owner, project ID and exact unknown operation ID. It renews
    ownership after process/root checks and dispatches no native command or chooser action. Open a prepared
    copy only by its private ID; inspect that exact native root before claiming it.
    """
    return await execute(lambda cancelled: client().session(action, owner, expected_project,
                                                           lease_id, ttl_seconds, cancelled,
                                                           pending_operation_id, prepared_copy_id))


@mcp.tool(annotations=WRITE)
async def project_prepare_copy(source: str, destination: str) -> CharacterPreparedCopy:
    """Copy a closed saved workspace .chproj tree and verify its file hashes before native opening.

    The source must not be open in Character Animator. This creates only a new workspace copy;
    use its prepared_copy_id with session_control to open the exact copy and then claim it.
    """
    return await execute(lambda cancelled: client().prepare_copy(source, destination, cancelled))


@mcp.tool(annotations=MUTATE)
async def project_save_copy(directory: str, name: str, lease_id: str) -> CharacterProject:
    """Native Save Project As into a new workspace project, then verify actual root and project files.

    directory is an existing parent; name is a safe ASCII project name. Does not edit the source
    scene or discard unsaved state. A missing original file hash remains explicitly unavailable.
    """
    return await execute(lambda cancelled: client().save_copy(directory, name, lease_id, cancelled))


@mcp.tool(annotations=MUTATE)
async def import_media(source: str, kind: Literal["puppet_psd", "audio_wav"],
                       lease_id: str) -> CharacterImportedMedia:
    """Import one workspace PSD puppet or WAV from a hash-pinned private copy.

    Adopts an exact already-open Import chooser; otherwise opens Import once. Submission is not completion.
    """
    return await execute(lambda cancelled: client().import_media(source, kind, lease_id, cancelled))


@mcp.tool(annotations=MUTATE)
async def scene_action(action: Literal["add_to_new_scene", "create_take_from_scene_audio",
                                      "compute_lip_sync", "export_png_wav"],
                       expected_fingerprint: str, lease_id: str) -> CharacterSceneAction:
    """Dispatch one fixed menu action against the current owned-project selection.

    Actions are add_to_new_scene, create_take_from_scene_audio, compute_lip_sync or export_png_wav.
    Copy the fingerprint from the latest inspect; scene/export completion remains unverified until inspected.
    """
    return await execute(lambda cancelled: client().scene_action(action, expected_fingerprint,
                                                                   lease_id, cancelled))


@mcp.tool(annotations=MUTATE)
async def scene_export(directory: str, name: str, expected_fingerprint: str,
                       lease_id: str) -> CharacterOperation:
    """Submit PNG Sequence and WAV for the current owned scene into a new workspace directory.

    The result confirms native chooser submission only. Inspect actual files with export_inspect after export.
    """
    return await execute(lambda cancelled: client().scene_export(
        directory, name, expected_fingerprint, lease_id, cancelled))


@mcp.tool(annotations=MUTATE)
async def project_select(name: str, item_type: Literal["puppet", "audio", "scene"],
                         extend: StrictBool, expected_fingerprint: str,
                         lease_id: str) -> CharacterProjectSelection:
    """Select one uniquely OCR-matched Project-panel row by name and type.

    extend controls additive selection; use the latest inspect fingerprint. OCR evidence is not a scene ID.
    """
    return await execute(lambda cancelled: client().project_select(
        name, item_type, extend, expected_fingerprint, lease_id, cancelled))


@mcp.tool(annotations=WRITE)
async def operation_control(action: Literal["inspect", "verify_copy", "continue_copy", "continue_import", "continue_export",
                                            "confirm_cancelled_import", "cancel_unsubmitted",
                                            "reconcile_unsubmitted_copy", "reconcile_copy_after_exit"],
                            operation_id: str, lease_id: str | None = None,
                            user_confirmed_cancelled: StrictBool = False) -> CharacterOperation:
    """Inspect or continue exact pending copy/import/export operations, verify a copy, or reconcile cancellation.

    continue_export resumes only the journaled Export Scene/Go To chooser and output directory.
    continue_import never replays File > Import and requires the existing chooser, except for the single
    recorded pre-menu focus rejection. confirm_cancelled_import requires explicit user attestation and
    matching process/root/source evidence; it issues no native command and is not native-verified cancellation.
    cancel_unsubmitted applies only to an observed unsubmitted Save As chooser.
    reconcile_unsubmitted_copy records only a closed, pre-submit initial Save As stage after stable source checks;
    it sends no native command and does not imply user cancellation.
    reconcile_copy_after_exit resolves only a submitted initial Save As after the exact app process has exited,
    no matching app instance is running, and the original destination remains absent; it verifies no copy.
    """
    return await execute(lambda cancelled: client().operation_control(
        action, operation_id, lease_id, cancelled, user_confirmed_cancelled))


@mcp.tool(annotations=READ)
async def export_inspect(recipe: str) -> CharacterMediaInspection:
    """Validate explicitly ordered native-export PNG/WAV/source hashes and exact dialogue sample timing.

    Frame rate/order are caller declarations; proprietary project/puppet files are opaque provenance.
    This inspects existing files and does not perform a native Character Animator export.
    """
    return await execute(lambda _: character_media.inspect_export(workspace(), recipe))


@mcp.tool(annotations=WRITE)
async def export_ingest(recipe: str, directory: str) -> CharacterHandoff:
    """Create a pinned media receipt and measured PCM audio descriptor for Unity cutscene delivery.

    Outputs go into a new directory. Preserves editable project/performance/XML provenance;
    does not establish native rigging, lip-sync correctness, playback or visual acceptance.
    """
    return await execute(lambda _: character_media.ingest_export(workspace(), recipe, directory))


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
