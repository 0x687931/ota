"""MicroPython OTA updater with GitHub release support.

This module implements a robust OTA (over-the-air) update system
for MicroPython devices.  It can fetch the latest release from a
GitHub repository (public or private), download a manifest that
lists files and their SHA256/CRC32 hashes, stream files to a staging
area, verify integrity and atomically swap them into place.  A simple
rollback mechanism restores the previous version if anything fails.

The implementation avoids loading large responses in memory and aims
for compatibility with both CPython (for testing) and MicroPython.
"""

import binascii
import json
import os
import hashlib
import hmac
import sys
from time import sleep

# Detect if running under MicroPython
MICROPYTHON = sys.implementation.name == "micropython"

if MICROPYTHON:
    import network  # type: ignore
    import urequests as requests  # type: ignore
    import machine  # type: ignore
else:  # pragma: no cover - running under CPython tests
    network = None  # type: ignore

    class _NoRequests:
        def get(self, *a, **k):  # pragma: no cover - not used in tests
            raise RuntimeError("urequests not available")

    requests = _NoRequests()  # type: ignore

    class _Machine:
        def reset(self):  # pragma: no cover - not used in tests
            pass

    machine = _Machine()  # type: ignore


CHUNK_SIZE = 1024  # default chunk size for downloads
VERSION_FILE = "version.json"
STAGE_DIR = ".ota_stage"
BACKUP_DIR = ".ota_backup"


def sha256_file(path: str, chunk_size: int = CHUNK_SIZE) -> str:
    """Compute the SHA256 of *path* streaming in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def crc32_file(path: str, chunk_size: int = CHUNK_SIZE) -> int:
    """Compute CRC32 of *path* streaming in chunks."""
    crc = 0
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            crc = binascii.crc32(block, crc)
    return crc & 0xFFFFFFFF


def sha1_git_blob_stream(total_size: int, reader, chunk_size: int = CHUNK_SIZE) -> str:
    """Compute Git blob SHA1 by streaming bytes from *reader*.

    Parameters
    ----------
    total_size: int
        Expected size of the blob in bytes.
    reader: callable
        Function that accepts ``chunk_size`` and yields byte chunks.
    chunk_size: int
        Size of chunks to request from ``reader``.
    """

    h = hashlib.sha1()
    header = b"blob " + str(total_size).encode() + b"\x00"
    h.update(header)
    remaining = total_size
    for chunk in reader(chunk_size):
        if not chunk:
            break
        remaining -= len(chunk)
        h.update(chunk)
    if remaining != 0:
        raise OTAError("size mismatch while hashing")
    return h.hexdigest()


class OTAError(Exception):
    """Custom exception for OTA update failures."""


class OTAUpdater:
    """Perform OTA updates from GitHub releases.

    Parameters
    ----------
    config: dict
        Configuration dictionary with keys:
            - ssid, password: Wi-Fi credentials
            - repo_owner, repo_name: GitHub repository information
            - tag: optional specific release tag; if absent the latest
              release is used
            - token: optional GitHub token for private repos
    chunk_size: int
        Size of chunks when streaming downloads.
    log: bool
        Enable log messages via ``print``.
    """

    def __init__(self, config: dict, chunk_size: int = CHUNK_SIZE, log: bool = True):
        self.config = config
        self.chunk_size = chunk_size
        self.log_enabled = log
        self.stage_dir = STAGE_DIR
        self.backup_dir = BACKUP_DIR
        self._ensure_dir(self.stage_dir)
        self._ensure_dir(self.backup_dir)
        self._startup_check()

    # ------------------------------------------------------------------
    # Utility helpers
    def _log(self, msg: str, end: str = "\n") -> None:
        if self.log_enabled:
            print(msg, end=end)

    @staticmethod
    def _ensure_dir(path: str) -> None:
        """Create directory *path* recursively if it does not exist."""
        parts = []
        while path and not os.path.isdir(path):
            parts.append(path)
            path = os.path.dirname(path)
        for p in reversed(parts):
            os.mkdir(p)

    @staticmethod
    def _dir_empty(path: str) -> bool:
        try:
            return not os.listdir(path)
        except OSError:
            return True

    def _startup_check(self) -> None:
        """Rollback or cleanup if previous update left state behind."""
        if not self._dir_empty(self.backup_dir):
            self._log("incomplete update detected; rolling back")
            self.rollback()
            self._cleanup_stage()
        elif not self._dir_empty(self.stage_dir):
            self._log("cleaning leftover staging files")
            self._cleanup_stage()

    # ------------------------------------------------------------------
    # Version management
    def _read_version(self):
        try:
            with open(VERSION_FILE) as f:
                return json.load(f)["version"]
        except Exception:
            return None

    def _write_version(self, version: str) -> None:
        tmp = VERSION_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"version": version}, f)
            f.flush()
            if hasattr(os, "fsync"):
                os.fsync(f.fileno())
        os.rename(tmp, VERSION_FILE)

    def _verify_manifest_signature(self, manifest: dict) -> None:
        key = self.config.get("manifest_key")
        if not key:
            return
        sig = manifest.get("signature")
        if not sig:
            raise OTAError("manifest missing signature")
        tmp = manifest.copy()
        tmp.pop("signature", None)
        data = json.dumps(tmp, sort_keys=True, separators=(",", ":")).encode()
        expected = hmac.new(key.encode(), data, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise OTAError("manifest signature mismatch")

    # ------------------------------------------------------------------
    # Wi-Fi and GitHub interaction
    def connect_wifi(self, retries: int = 5, delay: int = 2) -> None:
        """Connect to Wi-Fi with bounded retries."""
        if network is None:
            self._log("network module not available")
            return
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        if not sta.isconnected():
            sta.connect(self.config.get("ssid"), self.config.get("password"))
            attempt = 0
            while not sta.isconnected() and attempt < retries:
                sleep(delay * (2 ** attempt))
                attempt += 1
        if not sta.isconnected():
            raise OTAError("WiFi connection failed")
        self._log("Connected to WiFi: {}".format(sta.ifconfig()[0]))

    def _github_headers(self) -> dict:
        headers = {"Accept": "application/vnd.github+json"}
        token = self.config.get("token")
        if token:
            headers["Authorization"] = "token {}".format(token)
        return headers

    def _github_api(self, url: str):  # pragma: no cover - network not used in tests
        return requests.get(url, headers=self._github_headers())

    def _get_release(self) -> dict:  # pragma: no cover - network not used in tests
        owner = self.config["repo_owner"]
        repo = self.config["repo_name"]
        tag = self.config.get("tag")
        if tag:
            url = "https://api.github.com/repos/{}/{}/releases/tags/{}".format(owner, repo, tag)
        else:
            url = "https://api.github.com/repos/{}/{}/releases/latest".format(owner, repo)
        resp = self._github_api(url)
        if resp.status_code != 200:
            raise OTAError("Failed to fetch release: {}".format(resp.status_code))
        data = resp.json()
        resp.close()
        return data

    def _resolve_commit_sha(self, tag: str) -> str:  # pragma: no cover - network not used in tests
        owner = self.config["repo_owner"]
        repo = self.config["repo_name"]
        url = "https://api.github.com/repos/{}/{}/git/ref/tags/{}".format(owner, repo, tag)
        resp = self._github_api(url)
        if resp.status_code != 200:
            raise OTAError("Failed to resolve tag: {}".format(resp.status_code))
        ref = resp.json()
        resp.close()
        obj = ref["object"]
        if obj["type"] == "commit":
            return obj["sha"]
        tag_obj = "https://api.github.com/repos/{}/{}/git/tags/{}".format(owner, repo, obj["sha"])
        resp = self._github_api(tag_obj)
        if resp.status_code != 200:
            raise OTAError("Failed to resolve tag object: {}".format(resp.status_code))
        tag_data = resp.json()
        resp.close()
        return tag_data["object"]["sha"]

    def _fetch_tree(self, commit_sha: str):  # pragma: no cover - network not used in tests
        owner = self.config["repo_owner"]
        repo = self.config["repo_name"]
        url = "https://api.github.com/repos/{}/{}/git/trees/{}?recursive=1".format(owner, repo, commit_sha)
        resp = self._github_api(url)
        if resp.status_code != 200:
            raise OTAError("Failed to fetch tree: {}".format(resp.status_code))
        tree = resp.json().get("tree", [])
        resp.close()
        return tree

    def _allowed_path(self, path: str) -> bool:
        allow = self.config.get("paths")
        if not allow:
            return True
        for p in allow:
            if path == p or path.startswith(p.rstrip("/") + "/"):
                return True
        return False

    def _download_blob_to_stage(self, entry: dict):  # pragma: no cover - network not used in tests
        path = entry["path"]
        size = int(entry.get("size", 0))
        blob_sha = entry["sha"]
        dest = self._stage_path(path)
        self._ensure_dir(os.path.dirname(dest))
        owner = self.config["repo_owner"]
        repo = self.config["repo_name"]
        url = "https://api.github.com/repos/{}/{}/git/blobs/{}".format(owner, repo, blob_sha)
        headers = self._github_headers()
        headers["Accept"] = "application/vnd.github.raw"
        resp = requests.get(url, headers=headers, stream=True)
        if resp.status_code != 200:
            raise OTAError("Download failed: {}".format(resp.status_code))
        tmp = dest + ".tmp"
        with open(tmp, "wb") as f:
            def reader(n):
                while True:
                    block = resp.raw.read(n)
                    if not block:
                        break
                    f.write(block)
                    yield block
            digest = sha1_git_blob_stream(size, reader, self.chunk_size)
            f.flush()
            if hasattr(os, "fsync"):
                os.fsync(f.fileno())
        resp.close()
        if digest != blob_sha:
            raise OTAError("hash mismatch for {}".format(path))
        os.rename(tmp, dest)

    def _download_asset(self, url: str, dest: str, expected_sha=None, expected_crc=None, expected_size=None):  # pragma: no cover - network not used in tests
        headers = self._github_headers()
        headers["Accept"] = "application/octet-stream"
        resp = requests.get(url, headers=headers, stream=True)
        if resp.status_code != 200:
            raise OTAError("Download failed: {}".format(resp.status_code))
        tmp_path = dest + ".tmp"
        h = hashlib.sha256()
        crc = 0
        total = 0
        with open(tmp_path, "wb") as f:
            while True:
                block = resp.raw.read(self.chunk_size)
                if not block:
                    break
                total += len(block)
                h.update(block)
                crc = binascii.crc32(block, crc)
                f.write(block)
            f.flush()
            if hasattr(os, "fsync"):
                os.fsync(f.fileno())
        resp.close()
        if expected_size is not None and total != expected_size:
            raise OTAError("size mismatch for {}".format(dest))
        sha = h.hexdigest()
        crc = crc & 0xFFFFFFFF
        if expected_sha and sha != expected_sha:
            raise OTAError("hash mismatch for {}".format(dest))
        if not expected_sha and expected_crc is not None and crc != expected_crc:
            raise OTAError("crc mismatch for {}".format(dest))
        os.rename(tmp_path, dest)

    # ------------------------------------------------------------------
    def _stage_path(self, path: str) -> str:
        return "{}/{}".format(self.stage_dir, path)

    def _backup_path(self, path: str) -> str:
        return "{}/{}".format(self.backup_dir, path)

    def _verify_file(self, path: str, sha=None, size=None, crc=None) -> None:
        """Verify file at *path* against expected values."""
        st = os.stat(path)
        if size is not None and st.st_size != size:
            raise OTAError("size mismatch for {}".format(path))
        if sha:
            if sha256_file(path, self.chunk_size) != sha:
                raise OTAError("sha256 mismatch for {}".format(path))
        elif crc is not None:
            if crc32_file(path, self.chunk_size) != crc:
                raise OTAError("crc32 mismatch for {}".format(path))

    def _download_to_stage(self, fileinfo: dict):  # pragma: no cover - network not used in tests
        path = fileinfo["path"]
        url = fileinfo["url"]
        dest = self._stage_path(path)
        self._ensure_dir(os.path.dirname(dest))
        self._download_asset(url, dest, fileinfo.get("sha256"), fileinfo.get("crc32"), fileinfo.get("size"))
        self._verify_file(dest, fileinfo.get("sha256"), fileinfo.get("size"), fileinfo.get("crc32"))

    def _apply_update(self, manifest: dict) -> None:
        """Swap staged files into place atomically.

        On failure all files are rolled back from the backup directory.
        """
        applied = []
        try:
            for fi in manifest.get("files", []):
                path = fi["path"]
                staged = self._stage_path(path)
                target = path
                backup = self._backup_path(path)
                self._ensure_dir(os.path.dirname(target))
                self._ensure_dir(os.path.dirname(backup))
                if os.path.exists(target):
                    os.rename(target, backup)
                os.rename(staged, target)
                applied.append((target, backup))
            self._write_version(manifest.get("version", "0"))
        except Exception as exc:
            self._log("apply failed: {}".format(exc))
            self.rollback()
            raise
        else:
            self._cleanup_backups()
            self._cleanup_stage()

    def rollback(self) -> None:
        """Restore files from backup after a failed update."""
        for root, dirs, files in self._walk(self.backup_dir):
            for name in files:
                bpath = os.path.join(root, name)
                rel = bpath[len(self.backup_dir) + 1:]
                target = rel
                self._ensure_dir(os.path.dirname(target))
                if os.path.exists(target):
                    os.remove(target)
                os.rename(bpath, target)
        self._cleanup_backups()

    def _cleanup_stage(self) -> None:
        self._rmtree(self.stage_dir)
        self._ensure_dir(self.stage_dir)

    def _cleanup_backups(self) -> None:
        self._rmtree(self.backup_dir)
        self._ensure_dir(self.backup_dir)

    # ------------------------------------------------------------------
    # Lightweight replacements for os.walk and shutil.rmtree for MicroPython
    def _walk(self, base):
        dirs = []
        files = []
        for name in os.listdir(base):
            path = os.path.join(base, name)
            if os.path.isdir(path):
                dirs.append(name)
            else:
                files.append(name)
        yield base, dirs, files
        for d in dirs:
            for x in self._walk(os.path.join(base, d)):
                yield x

    def _rmtree(self, path):
        if not os.path.exists(path):
            return
        for root, dirs, files in self._walk(path):
            for f in files:
                os.remove(os.path.join(root, f))
            for d in dirs:
                os.rmdir(os.path.join(root, d))
        if path != ".":
            try:
                os.rmdir(path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # High level API
    def update(self):  # pragma: no cover - network not used in tests
        """Perform an update if a newer release exists."""
        self.connect_wifi()
        release = self._get_release()
        tag = release["tag_name"]
        manifest_asset = None
        for a in release.get("assets", []):
            if a.get("name") == "manifest.json":
                manifest_asset = a
                break
        manifest = None
        if manifest_asset:
            manifest_url = manifest_asset["url"]
            headers = self._github_headers()
            headers["Accept"] = "application/octet-stream"
            resp = requests.get(manifest_url, headers=headers)
            if resp.status_code != 200:
                raise OTAError("manifest download failed")
            manifest = resp.json()
            resp.close()
            self._verify_manifest_signature(manifest)
            version = manifest.get("version", tag)
        else:
            version = tag
        current_version = self._read_version()
        if current_version == version:
            self._log("Device already at version {}".format(current_version))
            return
        if manifest_asset:
            for fi in manifest.get("files", []):
                raw_url = "https://raw.githubusercontent.com/{}/{}/{}/{}".format(
                    self.config["repo_owner"],
                    self.config["repo_name"],
                    tag,
                    fi["path"]
                )
                fi["url"] = raw_url
                self._download_to_stage(fi)
        else:
            manifest = {"version": version, "files": []}
            commit_sha = self._resolve_commit_sha(tag)
            tree = self._fetch_tree(commit_sha)
            for entry in tree:
                if entry.get("type") != "blob":
                    continue
                if not self._allowed_path(entry["path"]):
                    continue
                if int(entry.get("size", 0)) == 0:
                    continue
                self._download_blob_to_stage(entry)
                manifest["files"].append({"path": entry["path"]})
        self._apply_update(manifest)
        if manifest.get("post_update"):
            self._run_hook(manifest["post_update"])
        self._log("Update to {} applied".format(version))
        machine.reset()

    def _run_hook(self, path):
        try:
            __import__(path.replace("/", ".").rstrip(".py"))
        except Exception as exc:
            self._log("post-update hook failed: {}".format(exc))

