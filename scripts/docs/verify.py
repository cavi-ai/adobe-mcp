"""Verify that built documentation is complete and matches its manifest."""

import json
import sys
from pathlib import Path

from .lib import (
    OUTPUT_REL,
    PRODUCT_ID,
    REPO_ROOT,
    UNRESOLVED_TOKEN,
    compute_content_sha256,
    list_files_recursive,
    navigation_paths,
    parse_options,
    release_input,
    resolve_release_identity,
)


def verify_documentation(values=None):
    values = values or {}
    release = resolve_release_identity(values)
    root = Path(values.get("output_root") or REPO_ROOT / OUTPUT_REL).resolve()
    paths = list_files_recursive(root)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("documentation manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "schemaVersion": 2,
        "package": PRODUCT_ID,
        "product": PRODUCT_ID,
        "version": release["version"],
        "contentSha256": compute_content_sha256(root, paths),
        "publicBasePath": f"/docs/{PRODUCT_ID}/v{release['version']}",
        "stableAlias": f"/docs/{PRODUCT_ID}",
        "release": {"tag": release["tag"], "commit": release["commit"]},
        "generatedAt": release["generated_at"],
    }
    if manifest != expected:
        raise ValueError("documentation manifest is inconsistent with built content")
    navigation = json.loads((root / "navigation.json").read_text(encoding="utf-8"))
    for page in navigation_paths(navigation):
        if not (root / page).is_file():
            raise ValueError(f"navigation path is not a file: {page}")
    for relative in paths:
        if UNRESOLVED_TOKEN.search((root / relative).read_text(encoding="utf-8")):
            raise ValueError(f"unresolved template token in {relative}")
    return manifest


def main(argv=None):
    values = parse_options(sys.argv[1:] if argv is None else argv)
    try:
        manifest = verify_documentation(
            {
                **release_input(values),
                **({"output_root": values["output"]} if "output" in values else {}),
            }
        )
    except (ValueError, OSError, KeyError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
