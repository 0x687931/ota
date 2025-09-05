"""Generate manifest.json for OTA releases.

This script is intended to run on a development machine.  It scans a
set of files, computes their SHA256 hashes and sizes and outputs a
manifest compatible with :mod:`ota_updater`.

Usage:
    python manifest_gen.py --version v1.2.3 file1.py lib/module.py ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import hmac

CHUNK_SIZE = 1024


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(CHUNK_SIZE)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def build_manifest(version: str, files: list[str], post_update: str | None, rollback: str | None) -> dict:
    manifest = {"version": version, "files": []}
    for path in files:
        info = {
            "path": path.replace("\\", "/"),
            "sha256": sha256(path),
            "size": os.path.getsize(path),
        }
        manifest["files"].append(info)
    if post_update:
        manifest["post_update"] = post_update
    if rollback:
        manifest["rollback"] = rollback
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate OTA manifest")
    parser.add_argument("files", nargs="+", help="files to include")
    parser.add_argument("--version", required=True, help="release version")
    parser.add_argument("--post-update", dest="post_update", help="post update hook script")
    parser.add_argument("--rollback", dest="rollback", help="rollback hook script")
    parser.add_argument("--key", help="HMAC key for signing the manifest")
    parser.add_argument("-o", "--output", default="manifest.json")
    args = parser.parse_args()
    manifest = build_manifest(args.version, args.files, args.post_update, args.rollback)
    if args.key:
        data = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        sig = hmac.new(args.key.encode(), data, hashlib.sha256).hexdigest()
        manifest["signature"] = sig
    with open(args.output, "w") as f:
        json.dump(manifest, f, indent=2)
    print("wrote", args.output)


if __name__ == "__main__":
    main()
