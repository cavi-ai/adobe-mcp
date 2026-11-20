"""Build the versioned documentation tree consumed by cavi-home."""

import json
import sys
from pathlib import Path

from .lib import (
    OUTPUT_REL,
    PRODUCT_ID,
    REPO_ROOT,
    SOURCE_REL,
    UNRESOLVED_TOKEN,
    compute_content_sha256,
    dump_json,
    list_files_recursive,
    navigation_paths,
    parse_options,
    release_input,
    resolve_release_identity,
    stamp_release_tokens,
)


def _expected_files(source_root, release):
    values = {}
    for source_path in list_files_recursive(source_root):
        output_path = source_path.removeprefix("pages/")
        stamped = stamp_release_tokens(
            (Path(source_root) / source_path).read_text(encoding="utf-8"), release
        )
        if UNRESOLVED_TOKEN.search(stamped):
            raise ValueError(f"unresolved template token in {source_path}")
        values[output_path] = stamped
    navigation = json.loads(values["navigation.json"])
    for page in navigation_paths(navigation):
        if page not in values:
            raise ValueError(f"navigation path missing: {page}")
    return values


def _prepare_output_tree(output_root, expected):
    output_root = Path(output_root)
    if not output_root.exists():
        for relative, text in expected.items():
            target = output_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return "fresh"
    actual_paths = list_files_recursive(output_root)
    expected_paths = sorted([*expected, "manifest.json"])
    if actual_paths == expected_paths:
        clean = all(
            (output_root / relative).read_text(encoding="utf-8") == text
            for relative, text in expected.items()
        )
        if clean:
            return "clean"
        _regenerate(output_root, expected)
        return "regenerated"
    raise ValueError(f"dirty output: {output_root}")


def _regenerate(output_root, expected):
    for target in sorted(output_root.rglob("*"), reverse=True):
        if target.is_file() or target.is_symlink():
            target.unlink()
        elif target.is_dir():
            target.rmdir()
    for relative, text in expected.items():
        target = output_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def build_documentation(values=None):
    release = resolve_release_identity(values or {})
    source_root = Path((values or {}).get("source_root") or REPO_ROOT / SOURCE_REL).resolve()
    output_root = Path((values or {}).get("output_root") or REPO_ROOT / OUTPUT_REL).resolve()
    if source_root == output_root or output_root.is_relative_to(source_root):
        raise ValueError("documentation output must be outside the source tree")
    expected = _expected_files(source_root, release)
    state = _prepare_output_tree(output_root, expected)
    content_sha = compute_content_sha256(output_root, list(expected))
    manifest = {
        "schemaVersion": 2,
        "package": PRODUCT_ID,
        "product": PRODUCT_ID,
        "version": release["version"],
        "contentSha256": content_sha,
        "publicBasePath": f"/docs/{PRODUCT_ID}/v{release['version']}",
        "stableAlias": f"/docs/{PRODUCT_ID}",
        "release": {"tag": release["tag"], "commit": release["commit"]},
        "generatedAt": release["generated_at"],
    }
    manifest_bytes = dump_json(manifest)
    manifest_path = output_root / "manifest.json"
    if (
        state in {"fresh", "regenerated"}
        or not manifest_path.exists()
        or manifest_path.read_bytes() != manifest_bytes
    ):
        manifest_path.write_bytes(manifest_bytes)
    return {"output_root": output_root, "manifest": manifest}


def main(argv=None):
    values = parse_options(sys.argv[1:] if argv is None else argv)
    try:
        result = build_documentation(
            {
                **release_input(values),
                **({"source_root": values["source"]} if "source" in values else {}),
                **({"output_root": values["output"]} if "output" in values else {}),
            }
        )
    except (ValueError, OSError, KeyError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    print(result["output_root"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
