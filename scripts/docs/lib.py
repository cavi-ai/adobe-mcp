"""Shared release identity and documentation path rules."""

import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_ID = "adobe-mcp"
RELEASE_REPOSITORY = "cavi-ai/adobe-mcp"
# What a checkout documents before the first vX.Y.Z tag exists.
UNRELEASED_VERSION = "0.0.0"
SOURCE_REL = f"docs/{PRODUCT_ID}/source"
PRODUCT_VERSION_TOKEN = "{{PRODUCT_VERSION}}"
RELEASE_TAG_TOKEN = "{{RELEASE_TAG}}"
RELEASE_COMMIT_TOKEN = "{{RELEASE_COMMIT}}"
UNRESOLVED_TOKEN = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")
STABLE_VERSION = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
COMMIT_SHA = re.compile(r"^[a-f0-9]{40}$")


def _documented_version():
    """The release this checkout documents: the newest vX.Y.Z tag reachable from HEAD."""
    try:
        tag = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*"],
            cwd=REPO_ROOT, check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return UNRELEASED_VERSION
    version = tag.removeprefix("v")
    if not STABLE_VERSION.fullmatch(version):
        raise ValueError(f"release tag {tag} is not vX.Y.Z")
    return version


DOCUMENTED_VERSION = _documented_version()
OUTPUT_REL = f"docs/{PRODUCT_ID}/v{DOCUMENTED_VERSION}"


def compare_portable_paths(left, right):
    return -1 if left < right else 1 if left > right else 0


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def default_release_identity():
    commit = _git("rev-parse", "HEAD")
    epoch = int(_git("show", "-s", "--format=%ct", commit))
    return resolve_release_identity(
        {
            "version": DOCUMENTED_VERSION,
            "tag": f"v{DOCUMENTED_VERSION}",
            "commit": commit,
            "source_date_epoch": epoch,
        }
    )


def resolve_release_identity(values=None):
    values = values or {}
    complete = all(
        values.get(key) is not None
        for key in ("version", "tag", "commit", "source_date_epoch")
    )
    defaults = None if complete else default_release_identity()
    version = values.get("version") or (defaults or {}).get("version") or DOCUMENTED_VERSION
    tag = values.get("tag") or (defaults or {}).get("tag") or f"v{version}"
    commit = values.get("commit") or (defaults or {}).get("commit")
    epoch = values.get("source_date_epoch")
    if epoch is None:
        epoch = (defaults or {}).get("source_date_epoch")
    if version != DOCUMENTED_VERSION:
        raise ValueError(f"release version must be {DOCUMENTED_VERSION}")
    if tag != f"v{version}":
        raise ValueError("release tag must match version")
    if not isinstance(commit, str) or not COMMIT_SHA.fullmatch(commit):
        raise ValueError("release commit must be a full lowercase SHA")
    if type(epoch) is not int or epoch < 0:
        raise ValueError("source date epoch must be a non-negative integer")
    return {
        "version": version,
        "tag": tag,
        "commit": commit,
        "source_date_epoch": epoch,
        "generated_at": datetime.fromtimestamp(epoch, UTC).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        ),
    }


def stamp_release_tokens(text, release):
    return (
        text.replace(PRODUCT_VERSION_TOKEN, release["version"])
        .replace(RELEASE_TAG_TOKEN, release["tag"])
        .replace(RELEASE_COMMIT_TOKEN, release["commit"])
    )


def parse_options(argv):
    values = {}
    index = 0
    while index < len(argv):
        option = argv[index]
        if option.startswith("--") and index + 1 < len(argv) and not argv[index + 1].startswith(
            "--"
        ):
            values[option[2:]] = argv[index + 1]
            index += 2
        else:
            index += 1
    return values


def release_input(values):
    result = {}
    for key, target in (
        ("version", "version"),
        ("tag", "tag"),
        ("commit", "commit"),
    ):
        if key in values:
            result[target] = values[key]
    if "source-date-epoch" in values:
        result["source_date_epoch"] = int(values["source-date-epoch"])
    return result


def list_files_recursive(root):
    root = Path(root)
    files = []

    def walk(directory, relative):
        entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        for entry in entries:
            path = Path(entry.path)
            name = f"{relative}/{entry.name}" if relative else entry.name
            if path.is_symlink():
                raise ValueError(f"documentation rejects symlink: {name}")
            if path.is_dir():
                walk(path, name)
            elif path.is_file():
                files.append(name)
            else:
                raise ValueError(f"documentation rejects non-file: {name}")

    walk(root, "")
    return sorted(files, key=_portable_key)


def _portable_key(value):
    return value


def compute_content_sha256(root, relative_paths):
    digest = hashlib.sha256()
    for relative in sorted(
        (item for item in relative_paths if item != "manifest.json"), key=_portable_key
    ):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((Path(root) / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def navigation_paths(navigation):
    if not isinstance(navigation, dict) or not isinstance(navigation.get("sections"), list):
        raise ValueError("navigation.json missing sections array")  # noqa: TRY004
    pages = []
    for section in navigation["sections"]:
        if not isinstance(section, dict) or not isinstance(section.get("pages"), list):
            raise ValueError("navigation.json sections need pages arrays")  # noqa: TRY004
        for page in section["pages"]:
            path = page.get("path") if isinstance(page, dict) else None
            if (
                not isinstance(path, str)
                or not path
                or path.startswith("/")
                or ".." in path.split("/")
            ):
                raise ValueError("navigation.json contains an unsafe page path")
            pages.append(path)
    if len(set(pages)) != len(pages):
        raise ValueError("navigation.json contains duplicate page paths")
    return pages


def safe_archive_path(relative):
    portable = str(relative).replace(os.sep, "/")
    if not portable or portable.startswith("/") or ".." in portable.split("/"):
        raise ValueError(f"unsafe documentation archive path: {relative}")
    return portable


def create_release_envelope(release, repository, artifact_sha256):
    artifact_name = f"{PRODUCT_ID}-docs-{release['tag']}.tar.gz"
    return {
        "schemaVersion": 1,
        "slug": PRODUCT_ID,
        "kind": "product-docs",
        "version": release["version"],
        "tag": release["tag"],
        "repository": repository,
        "commit": release["commit"],
        "artifact": {
            "url": (
                f"https://github.com/{repository}/releases/download/"
                f"{release['tag']}/{artifact_name}"
            ),
            "sha256": artifact_sha256,
            "format": "tar.gz",
        },
    }


def write_identical_or_absent(target, contents):
    target = Path(target)
    if target.exists():
        if target.read_bytes() != contents:
            raise ValueError(f"refusing to overwrite non-identical release artifact: {target}")
        return
    target.write_bytes(contents)


def dump_json(value):
    return (json.dumps(value, indent=2, sort_keys=False) + "\n").encode()
