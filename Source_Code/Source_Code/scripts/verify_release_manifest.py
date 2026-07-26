#!/usr/bin/env python3
"""Verify the untouched release before any reproduction output is regenerated."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    default_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root,
        help="release root containing README.md, the PDF, and Source_Code/",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    manifest_path = root / "Source_Code" / "RELEASE_MANIFEST.json"
    if not manifest_path.is_file():
        raise SystemExit(f"release manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise SystemExit("release manifest has no file entries")

    declared: set[str] = set()
    failures: list[str] = []
    for entry in entries:
        rel = entry.get("path")
        if not isinstance(rel, str) or not rel or rel in declared:
            failures.append(f"invalid or duplicate manifest path: {rel!r}")
            continue
        declared.add(rel)
        path = root / rel
        if not path.is_file():
            failures.append(f"missing: {rel}")
            continue
        expected_bytes = int(entry["bytes"])
        actual_bytes = path.stat().st_size
        if actual_bytes != expected_bytes:
            failures.append(
                f"size: {rel} expected {expected_bytes}, got {actual_bytes}"
            )
        expected_hash = str(entry["sha256"]).lower()
        actual_hash = sha256(path)
        if actual_hash != expected_hash:
            failures.append(
                f"sha256: {rel} expected {expected_hash}, got {actual_hash}"
            )

    # Git's private metadata is not part of the release tree.  Every other
    # file must be declared, including dotfiles such as Source_Code/.gitignore.
    environment_metadata = {
        ".git",
        ".ipynb_checkpoints",
        "__pycache__",
        ".pytest_cache",
    }
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and not environment_metadata.intersection(path.relative_to(root).parts)
        and path.name not in {".DS_Store", "Thumbs.db"}
    }
    manifest_rel = manifest_path.relative_to(root).as_posix()
    expected_tree = declared | {manifest_rel}
    for rel in sorted(actual - expected_tree):
        failures.append(f"unexpected: {rel}")
    for rel in sorted(expected_tree - actual):
        failures.append(f"missing from tree: {rel}")

    if failures:
        print("RELEASE MANIFEST: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"RELEASE MANIFEST: PASS {len(entries)}/{len(entries)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
