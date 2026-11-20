"""Session packets, native requests and receipts stay byte-shape identical across hosts."""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from owned_copy_fixtures import SVG, ContractBridge, IllustratorContractBridge, png_bytes

from cavi_adobe_mcp.core.extendscript import AdobeCommandRejected, AdobeOutcomeUnknown

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "owned_copies"


def make(app, root):
    return (ContractBridge if app == "photoshop" else IllustratorContractBridge)(root)


def shape(value, root):
    """Replace run-specific paths, tokens and digests; keep keys, order, types and constants."""
    if isinstance(value, dict):
        return {key: "<int>" if key in ("device", "inode") else shape(child, root)
                for key, child in value.items()}
    if isinstance(value, list):
        return [shape(child, root) for child in value]
    if isinstance(value, str):
        text = value.replace(str(root), "<root>")
        text = re.sub(r"/[^\"' ]*?/(cavi-adobe-command-)[A-Za-z0-9_]{8}", r"<tmp>/\1<id>", text)
        text = re.sub(r"(source-|canvas-recipe-|\.cavi-adobe-export-)[A-Za-z0-9_]{8}", r"\1<id>", text)
        text = re.sub(r"\b[0-9a-f]{64}\b", "<sha256>", text)
        return re.sub(r"\b[0-9a-f]{32}\b", "<hex32>", text)
    return value


def packet(host, session):
    return json.loads((host.workspace / ".adobe-authoring" / (session + ".json")).read_text())


def records(root):
    """Drive each host through its owned-copy lifecycle and return the normalized records."""
    out = {}
    root = Path(root).resolve()
    for app, source, destination, export, kind in (
            ("photoshop", "source.png", "working.psd", "out.png", None),
            ("illustrator", "source.svg", "working.ai", "out.svg", "svg")):
        folder = root / app
        folder.mkdir()
        host = make(app, folder)
        (folder / "source.png").write_bytes(png_bytes())
        (folder / "source.svg").write_text(SVG)
        opened = host.open_copy(source, destination)
        session = opened["session_id"]
        host.add_group(session, "Head")
        host.add_rectangle(session, "Box", [0, 0, 1, 1], [1, 2, 3])
        exported = (host.export(session, export, usage="color") if kind is None
                    else host.export(session, export, kind=kind, usage="color"))
        out[app] = shape({
            "requests": host.requests,
            "open_result": opened,
            "export_result": exported,
            "packet": packet(host, session),
            "receipt_file": json.loads((folder / (export + ".receipt.json")).read_text()),
        }, folder)
        closed = host.close_copy(session)
        out[app]["close_state"] = packet(host, session)["state"]
        out[app]["close_result_keys"] = list(closed)
    folder = root / "canvas"
    folder.mkdir()
    host = make("photoshop", folder)
    canvas = host.create_canvas(320, 180, "canvas.psd")
    out["canvas"] = shape({
        "requests": host.requests, "result": canvas,
        "packet": packet(host, canvas["session_id"]),
        "recipe": json.loads(
            Path(packet(host, canvas["session_id"])["source"]).read_text())}, folder)
    folder = root / "transitions"
    folder.mkdir()
    host = make("photoshop", folder)
    (folder / "source.png").write_bytes(png_bytes())
    session = host.open_copy("source.png", "working.psd")["session_id"]
    states = [packet(host, session)["state"]]
    host.error = AdobeCommandRejected("rejected by native preflight")
    try:
        host.add_group(session, "Head")
    except AdobeCommandRejected:
        pass
    states.append(packet(host, session)["state"])
    states.append((folder / "pending.json").exists())
    host.error = AdobeOutcomeUnknown("lost reply")
    try:
        host.add_group(session, "Head")
    except AdobeOutcomeUnknown:
        pass
    states.append(packet(host, session)["state"])
    states.append((folder / "pending.json").exists())
    out["transitions"] = {"states": states}
    return out


class OwnedCopyGoldenTests(unittest.TestCase):
    def test_owned_copy_records_match_golden(self):
        with tempfile.TemporaryDirectory() as folder:
            actual = records(Path(folder))
        for name, value in actual.items():
            with self.subTest(record=name):
                expected = json.loads((FIXTURES / (name + ".json")).read_text())
                self.assertEqual(json.dumps(value), json.dumps(expected))


if __name__ == "__main__":
    if sys.argv[1:2] == ["capture"]:
        target = Path(sys.argv[2])
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            for name, value in records(Path(folder)).items():
                (target / (name + ".json")).write_text(json.dumps(value, indent=1) + "\n")
    else:
        unittest.main()
