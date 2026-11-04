"""Compile the fixed Swift helpers on first use, each build pinned to its exact source."""

import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import uuid
from pathlib import Path

from cavi_mcp_core.journal import write_json

# Cold optimized builds can take over a minute on Intel Macs.
BUILD_TIMEOUT = 300


class HelperError(Exception):
    """The helper could not be built or its cached build cannot be trusted."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def sha256(path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _trusted(output: Path, receipt: Path, identity: str) -> bool:
    if not output.is_file() or output.is_symlink() or not receipt.is_file() or receipt.is_symlink():
        return False
    try:
        return json.loads(receipt.read_text()) == {"source_sha256": identity,
                                                   "sha256": sha256(output)}
    except (OSError, ValueError):
        return False


def build_helper(source: Path, directory: Path, name: str) -> Path:
    """The executable compiled from ``source``, cached in ``directory/native/<source sha256>``.

    A cached build is used only when its receipt names both the source and the binary.
    A cache holding anything else is refused, never replaced. Builds of one source are
    serialized, so concurrent servers never compile into the same directory at once.
    """
    compiler = shutil.which("swiftc")
    if not compiler:
        raise HelperError("Install the Swift command-line compiler (xcode-select --install) "
                          "to build the native helper", "NATIVE_HELPER_UNAVAILABLE")
    if not source.is_file() or source.is_symlink():
        raise HelperError("The native helper source is missing", "NATIVE_HELPER_UNAVAILABLE")
    identity = sha256(source)
    cache = Path(directory) / "native"
    build = cache / identity
    build.mkdir(mode=0o700, parents=True, exist_ok=True)
    if cache.is_symlink() or build.is_symlink():
        raise HelperError("The native helper cache must not be a symbolic link",
                          "NATIVE_HELPER_UNSAFE")
    output, receipt = build / name, build / "build.json"
    with (build / "build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if output.exists() or output.is_symlink() or receipt.exists() or receipt.is_symlink():
            if _trusted(output, receipt, identity):
                return output
            raise HelperError(f"The native helper cache failed its integrity check; remove {build} "
                              "to rebuild it", "NATIVE_HELPER_UNSAFE")
        temporary = build / f".{name}-{uuid.uuid4().hex}.tmp"
        try:
            try:
                result = subprocess.run([compiler, "-O", str(source), "-o", str(temporary)],
                                        stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                        timeout=BUILD_TIMEOUT, check=False)
            except subprocess.TimeoutExpired as exc:
                raise HelperError("Native helper compilation timed out",
                                  "NATIVE_HELPER_BUILD_FAILED") from exc
            if result.returncode or not temporary.is_file():
                raise HelperError("Native helper compilation failed: " + result.stderr[-2000:],
                                  "NATIVE_HELPER_BUILD_FAILED")
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            os.replace(temporary, output)
            write_json(receipt, {"source_sha256": identity, "sha256": sha256(output)})
        finally:
            temporary.unlink(missing_ok=True)
    return output
