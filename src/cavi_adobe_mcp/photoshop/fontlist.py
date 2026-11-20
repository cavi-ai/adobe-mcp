"""Photoshop's font list: where each installed font file lies, and which kind of font that makes it.

Photoshop writes one record per font to ``AdobeFnt_OSFonts.lst``. Only the list is read; font
files are never opened and paths from the list are never followed.
"""

from __future__ import annotations

import os
import posixpath
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

MAX_FONT_LIST_BYTES = 64 * 1024 * 1024
HEADER = "%!Adobe-FontList"
SOURCES = ("adobe_fonts", "system", "local", "application", "unknown")
_APPLICATION_FOLDER = re.compile(r"Adobe Photoshop \d{4}")


def parse_font_list(text: str, limit: int = MAX_FONT_LIST_BYTES) -> dict[str, str | None]:
    """Map each PostScript name to its font file path, or None when the record names no usable file.

    A record runs from ``%BeginFont`` to ``%EndFont`` and holds ``FontName:`` and
    ``OutlineFileName:file://<path>#...`` lines. Records without a name or without an end are
    skipped; the first record of a name wins.
    """
    if not isinstance(text, str):
        raise TypeError("Font list must be text")
    if len(text) > limit:
        raise ValueError("Font list exceeds the 64 MB limit")
    lines = text.splitlines()
    if not lines or not lines[0].startswith(HEADER):
        raise ValueError("Font list does not start with %!Adobe-FontList")
    fonts: dict[str, str | None] = {}
    record = None
    for line in lines[1:]:
        if line == "%BeginFont":
            record = {}
        elif line == "%EndFont":
            if record and record.get("name"):
                fonts.setdefault(record["name"], _file_path(record.get("file")))
            record = None
        elif record is not None:
            key, _, value = line.partition(":")
            if key == "FontName":
                record.setdefault("name", value)
            elif key == "OutlineFileName":
                record.setdefault("file", value)
    return fonts


def _file_path(url):
    if not isinstance(url, str) or not url.startswith("file:///"):
        return None
    try:
        path = unquote(url[len("file://"):].split("#", 1)[0], errors="strict")
    except UnicodeDecodeError:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in path) or len(path) > 4096:
        return None
    return path


def classify_font_file(path: str | None, *, home: Path, application: str | None = None) -> str:
    """The font source for a file path: adobe_fonts, system, local, application or unknown."""
    if not path or not path.startswith("/") or ".." in path.split("/"):
        return "unknown"
    path = posixpath.normpath(path)
    home = str(home)
    livetype = home + "/Library/Application Support/Adobe/CoreSync/plugins/livetype/"
    if path.startswith((livetype + ".r/", livetype + ".w/")):
        return "adobe_fonts"
    if path.startswith("/System/Library/"):
        return "system"
    if path.startswith(("/Library/Fonts/", home + "/Library/Fonts/")):
        return "local"
    if application and path.startswith(posixpath.normpath(application) + "/"):
        return "application"
    return "unknown"


def font_list_location(application: str, home: Path) -> Path | None:
    """Photoshop's font list for the installed application, or None when its year is unknown."""
    app = Path(application)
    for name in (app.stem, app.parent.name):
        if _APPLICATION_FOLDER.fullmatch(name):
            return (home / "Library" / "Application Support" / "Adobe" / name
                    / "CT Font Cache" / "AdobeFnt_OSFonts.lst")
    return None


def read_font_list(path: Path) -> tuple[dict[str, str | None], str] | None:
    """Read the list at ``path`` and its modification time; None when the file is absent."""
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Font list is not a regular file")
    if before.st_size > MAX_FONT_LIST_BYTES:
        raise ValueError("Font list exceeds the 64 MB limit")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("Font list changed while it was opened")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_FONT_LIST_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_FONT_LIST_BYTES:
        raise ValueError("Font list exceeds the 64 MB limit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("Font list is not valid UTF-8") from exc
    modified = datetime.fromtimestamp(opened.st_mtime, UTC).isoformat().replace("+00:00", "Z")
    return parse_font_list(text), modified
