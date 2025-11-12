#!/usr/bin/env python3
# manifest_gen.py
import argparse, binascii, hashlib, hmac, io, json, os, time
from pathlib import Path

# Note: INCLUDE list is now set from CLI args, not hard-coded
# Default includes ota.py and main.py if no --include or --file-list provided
DEFAULT_INCLUDE = ["ota.py", "main.py"]


def want(path: str, include_list) -> bool:
    """Check if path matches any pattern in include_list."""
    # If no include list specified, allow all (rely on exclude filtering)
    if not include_list:
        return True
    # Otherwise check against include patterns
    for inc in include_list:
        if path == inc or path.startswith(inc.rstrip("/") + "/"):
            return True
    return False


def sha256_crc32(path, chunk=1024 * 256):
    h = hashlib.sha256()
    crc = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
            crc = binascii.crc32(b, crc)
    return h.hexdigest(), crc & 0xFFFFFFFF

def norm(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()

def build_manifest(version, files, deletes, post_update, key):
    m = {
        "version": version,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": files
    }
    if deletes:
        m["deletes"] = deletes
    if post_update:
        m["post_update"] = post_update
    if key:
        tmp = m.copy()
        tmp.pop("signature", None)
        data = json.dumps(tmp, sort_keys=True, separators=(",", ":")).encode()
        sig = hmac.new(key.encode(), data, hashlib.sha256).hexdigest()
        m["signature"] = sig
    return m

def main():
    ap = argparse.ArgumentParser(description="Generate OTA manifest.json")
    ap.add_argument("--root", default=".", help="project root to scan")
    ap.add_argument("--version", default=os.environ.get("GITHUB_REF_NAME") or "dev")
    ap.add_argument("--out", default="manifest.json")
    ap.add_argument("--key", default=os.environ.get("MANIFEST_KEY"), help="shared secret for signature")
    ap.add_argument("--file-list", default=None, help="text file with one path per line")
    ap.add_argument("--include", nargs="*", default=None, help="explicit file globs")
    ap.add_argument(
        "--exclude",
        nargs="*",
        default=[
            ".git*",  # ignore files like .gitignore; nested dirs handled separately
            ".ota_*",
            "__pycache__",
            "*.pyc",
            "*.pyo",
        ],
    )
    ap.add_argument("--deletes", default=None, help="text file of paths to delete")
    ap.add_argument("--post-update", default=None, help="module path to import after apply, eg hooks/post.py")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    chosen = set()

    # Determine include list for want() filtering
    # When using --file-list or --include, those paths are already filtered
    # When using default (rglob), apply DEFAULT_INCLUDE filtering
    use_want_filter = False
    include_patterns = None

    if args.file_list:
        # Explicit file list: no additional filtering needed
        for line in Path(args.file_list).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                chosen.add(root.joinpath(line).resolve())
        use_want_filter = False
    elif args.include:
        # Explicit include patterns: already filtered by glob
        import glob
        for pat in args.include:
            for p in glob.glob(str(root.joinpath(pat)), recursive=True):
                chosen.add(Path(p).resolve())
        use_want_filter = False
    else:
        # Default: include all files but filter with DEFAULT_INCLUDE
        for p in root.rglob("*"):
            if p.is_file():
                chosen.add(p.resolve())
        use_want_filter = True
        include_patterns = DEFAULT_INCLUDE

    # apply excludes
    def excluded(p: Path) -> bool:
        rel = norm(p, root)
        if ".git" in Path(rel).parts:  # fully ignore repository metadata
            return True
        for pat in args.exclude or []:
            if Path(rel).match(pat):
                return True
        # never include the manifest we are writing
        return rel == args.out or rel.endswith("/" + args.out)

    files = []
    for p in sorted(chosen):
        if excluded(p):
            continue
        rel = norm(p, root)
        # Only apply want() filter when using default mode (no CLI flags)
        if use_want_filter and not want(rel, include_patterns):
            continue
        size = p.stat().st_size
        sha, crc = sha256_crc32(p)
        files.append({"path": rel, "size": size, "sha256": sha, "crc32": crc})

    deletes = None
    if args.deletes:
        # For deletes, only filter if using default mode
        deletes = [
            ln.strip()
            for ln in Path(args.deletes).read_text().splitlines()
            if ln.strip() and not ln.startswith("#") and
               (not use_want_filter or want(ln.strip(), include_patterns))
        ]

    manifest = build_manifest(args.version, files, deletes, args.post_update, args.key)

    out_path = root.joinpath(args.out)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, sort_keys=True, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)
    print("Wrote", out_path)

if __name__ == "__main__":
    main()
