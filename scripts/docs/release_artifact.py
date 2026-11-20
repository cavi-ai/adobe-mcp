"""Create the immutable product-docs release artifact and dispatch envelope."""

import gzip
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

from .lib import (
    PRODUCT_ID,
    RELEASE_REPOSITORY,
    create_release_envelope,
    dump_json,
    list_files_recursive,
    parse_options,
    release_input,
    resolve_release_identity,
    safe_archive_path,
    write_identical_or_absent,
)
from .verify import verify_documentation


def _tar_bytes(entries, epoch):
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, contents in entries:
            info = tarfile.TarInfo(name=safe_archive_path(name))
            info.size = len(contents)
            info.mode = 0o644
            info.mtime = epoch
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.type = tarfile.REGTYPE
            archive.addfile(info, io.BytesIO(contents))
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", mtime=epoch, compresslevel=9) as stream:
        stream.write(tar.getvalue())
    return compressed.getvalue()


def create_product_docs_release_artifact(values):
    release = resolve_release_identity(values)
    repository = values.get("repository")
    if repository != RELEASE_REPOSITORY:
        raise ValueError("invalid release repository")
    docs_root = Path(values["docs_root"]).resolve()
    verify_documentation(
        {
            "version": release["version"],
            "tag": release["tag"],
            "commit": release["commit"],
            "source_date_epoch": release["source_date_epoch"],
            "output_root": docs_root,
        }
    )
    manifest = {
        "schemaVersion": 1,
        "slug": PRODUCT_ID,
        "kind": "product-docs",
        "version": release["version"],
        "tag": release["tag"],
        "repository": repository,
        "commit": release["commit"],
    }
    entries = [("cavi-release.json", dump_json(manifest))]
    archive_root = f"docs/{PRODUCT_ID}/v{release['version']}"
    for relative in list_files_recursive(docs_root):
        entries.append(
            (f"{archive_root}/{relative}", (docs_root / relative).read_bytes())
        )
    entries.sort(key=lambda item: item[0])
    archive = _tar_bytes(entries, release["source_date_epoch"])
    digest = hashlib.sha256(archive).hexdigest()
    artifact_name = f"{PRODUCT_ID}-docs-{release['tag']}.tar.gz"
    output = Path(values["output_directory"]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifact_path = output / artifact_name
    checksum_path = output / f"{artifact_name}.sha256"
    write_identical_or_absent(artifact_path, archive)
    write_identical_or_absent(checksum_path, f"{digest}  {artifact_name}\n".encode())
    return {
        "artifact_name": artifact_name,
        "artifact_path": artifact_path,
        "checksum_path": checksum_path,
        "artifact_sha256": digest,
        "manifest": manifest,
        "envelope": create_release_envelope(release, repository, digest),
    }


def main(argv=None):
    values = parse_options(sys.argv[1:] if argv is None else argv)
    try:
        result = create_product_docs_release_artifact(
            {
                **release_input(values),
                "docs_root": values["docs-root"],
                "output_directory": values["output"],
                "repository": values["repository"],
            }
        )
    except (ValueError, OSError, KeyError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    print(json.dumps(result["envelope"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
