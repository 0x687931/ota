#!/usr/bin/env python3
"""
Delta Generation Tool for OTA Updates

Generates binary deltas between file versions to minimize update bandwidth.
Run this on your development machine before creating releases.

Usage:
    python delta_gen.py --old v1.0.0 --new v1.1.0 --output .deltas/

This will:
1. Check out both versions
2. Compare files
3. Generate deltas for changed files
4. Save deltas to output directory
"""

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

# Import delta creation from delta.py
sys.path.insert(0, str(Path(__file__).parent))
from delta import create_delta, estimate_delta_size


def get_git_file(repo_path, ref, file_path):
    """Get file content from a specific git ref."""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "show", f"{ref}:{file_path}"],
            capture_output=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError:
        return None


def list_git_files(repo_path, ref, include_patterns=None):
    """List all files in a git ref."""
    result = subprocess.run(
        ["git", "-C", repo_path, "ls-tree", "-r", "--name-only", ref],
        capture_output=True,
        check=True,
        text=True
    )

    files = result.stdout.strip().split("\n")

    if include_patterns:
        filtered = []
        for f in files:
            if any(f.startswith(p.rstrip("/")) or f == p for p in include_patterns):
                filtered.append(f)
        files = filtered

    return files


def should_generate_delta(old_size, new_size, delta_size):
    """Decide if delta is worth it based on size savings."""
    # Only use delta if it saves at least 30% and is smaller than new file
    if delta_size >= new_size:
        return False
    savings = (new_size - delta_size) / new_size
    return savings >= 0.3


def main():
    parser = argparse.ArgumentParser(
        description="Generate binary deltas for OTA updates"
    )
    parser.add_argument("--repo", default=".", help="Git repository path")
    parser.add_argument("--old", required=True, help="Old version ref (tag/commit)")
    parser.add_argument("--new", required=True, help="New version ref (tag/commit)")
    parser.add_argument("--output", required=True, help="Output directory for deltas")
    parser.add_argument(
        "--include",
        nargs="*",
        default=["main.py", "lib/", "ota.py"],
        help="Files/directories to include",
    )
    parser.add_argument(
        "--min-size",
        type=int,
        default=1024,
        help="Minimum file size to delta (bytes)",
    )
    parser.add_argument(
        "--block-size",
        type=int,
        default=512,
        help="Block size for delta algorithm",
    )

    args = parser.parse_args()

    repo_path = Path(args.repo).resolve()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating deltas from {args.old} to {args.new}")
    print(f"Repository: {repo_path}")
    print(f"Output: {output_dir}")

    # Get file lists from both versions
    print("\nAnalyzing changes...")
    old_files = set(list_git_files(repo_path, args.old, args.include))
    new_files = set(list_git_files(repo_path, args.new, args.include))

    # Find changed files
    common_files = old_files & new_files
    added_files = new_files - old_files
    removed_files = old_files - new_files

    print(f"  Common files: {len(common_files)}")
    print(f"  Added files: {len(added_files)}")
    print(f"  Removed files: {len(removed_files)}")

    # Generate deltas for changed files
    deltas_created = 0
    total_old_size = 0
    total_new_size = 0
    total_delta_size = 0

    print("\nGenerating deltas...")
    for file_path in sorted(common_files):
        old_content = get_git_file(repo_path, args.old, file_path)
        new_content = get_git_file(repo_path, args.new, file_path)

        if old_content is None or new_content is None:
            continue

        # Skip if unchanged
        if old_content == new_content:
            continue

        # Skip if too small
        if len(new_content) < args.min_size:
            print(f"  Skip {file_path}: too small ({len(new_content)} bytes)")
            continue

        # Create temp files for delta generation
        old_temp = output_dir / f"{file_path.replace('/', '_')}.old"
        new_temp = output_dir / f"{file_path.replace('/', '_')}.new"

        old_temp.write_bytes(old_content)
        new_temp.write_bytes(new_content)

        # Generate delta
        try:
            delta_filename = f"{file_path.replace('/', '_')}.delta"
            delta_path = output_dir / delta_filename

            delta_data = create_delta(
                str(old_temp),
                str(new_temp),
                str(delta_path),
                block_size=args.block_size,
            )

            delta_size = len(delta_data)
            old_size = len(old_content)
            new_size = len(new_content)

            # Decide if delta is beneficial
            if should_generate_delta(old_size, new_size, delta_size):
                savings = ((new_size - delta_size) / new_size) * 100
                print(
                    f"  ✓ {file_path}: {new_size} → {delta_size} bytes ({savings:.1f}% savings)"
                )
                deltas_created += 1
                total_old_size += old_size
                total_new_size += new_size
                total_delta_size += delta_size
            else:
                # Delta not beneficial, remove it
                delta_path.unlink()
                print(
                    f"  ✗ {file_path}: Delta not beneficial ({delta_size} vs {new_size} bytes)"
                )

        except Exception as e:
            print(f"  Error creating delta for {file_path}: {e}")

        finally:
            # Cleanup temp files
            old_temp.unlink(missing_ok=True)
            new_temp.unlink(missing_ok=True)

    # Generate delta manifest
    manifest_path = output_dir / "delta_manifest.json"
    import json

    manifest = {
        "old_ref": args.old,
        "new_ref": args.new,
        "deltas": deltas_created,
        "total_new_size": total_new_size,
        "total_delta_size": total_delta_size,
        "savings_bytes": total_new_size - total_delta_size,
        "savings_percent": (
            ((total_new_size - total_delta_size) / total_new_size * 100)
            if total_new_size > 0
            else 0
        ),
    }

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # Summary
    print(f"\n{'='*60}")
    print(f"Delta Generation Summary")
    print(f"{'='*60}")
    print(f"Deltas created: {deltas_created}")
    print(f"Total new file size: {total_new_size:,} bytes")
    print(f"Total delta size: {total_delta_size:,} bytes")
    print(
        f"Bandwidth savings: {total_new_size - total_delta_size:,} bytes ({manifest['savings_percent']:.1f}%)"
    )
    print(f"\nDelta files saved to: {output_dir}")
    print(f"Manifest: {manifest_path}")

    # Provide deployment instructions
    print(f"\n{'='*60}")
    print("Deployment Instructions")
    print(f"{'='*60}")
    print(
        "1. Upload delta files to your repository in the .deltas/ directory:"
    )
    print(f"   git add {output_dir}")
    print(f"   git commit -m 'Add deltas for {args.new}'")
    print("   git push")
    print("\n2. Enable delta updates in device configuration:")
    print('   {"enable_delta_updates": true}')


if __name__ == "__main__":
    main()
