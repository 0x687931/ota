"""MicroPython friendly OTA client with dual update channels.

This module implements an ``OtaClient`` class that can update a device
from either the latest GitHub release (``stable`` channel) or from the tip
of a development branch (``developer`` channel).  Each downloaded file is
streamed through a Git style SHA1 verifier and written to a staging
directory before being atomically swapped into place with rollback
support.

The implementation uses only modules available on MicroPython but falls
back to CPython equivalents when executed on a development machine.  All
network operations are concentrated in the private ``_get`` method to make
unit testing easy by monkeypatching.
"""

import json
import os
import sys
from time import sleep

# Detect if running under MicroPython
MICROPYTHON = sys.implementation.name == "micropython"

if MICROPYTHON:
    import uhashlib as hashlib  # type: ignore
    import urequests as requests  # type: ignore
    import network  # type: ignore
    import machine  # type: ignore
else:  # pragma: no cover - running under CPython tests
    import hashlib  # type: ignore

    class _NoRequests:  # minimal stub
        def get(self, *a, **k):  # pragma: no cover
            raise RuntimeError("urequests not available")

    requests = _NoRequests()  # type: ignore
    network = None  # type: ignore

    class _Machine:
        def reset(self):  # pragma: no cover - not used in tests
            pass

    machine = _Machine()  # type: ignore


VERSION_FILE = "version.json"
STAGE_DIR = ".ota_stage"
BACKUP_DIR = ".ota_backup"


class OTAError(Exception):
    """Custom exception for OTA related failures."""


def git_blob_sha1_stream(total_size, reader, chunk):
    """Compute Git blob SHA1 while streaming data from ``reader``."""

    h = hashlib.sha1()
    h.update(b"blob " + str(total_size).encode() + b"\x00")
    remaining = total_size
    for data in reader(chunk):
        remaining -= len(data)
        h.update(data)
    if remaining != 0:
        raise ValueError("Size mismatch")
    return h.hexdigest()


def http_reader(resp):
    def _yield(n):
        while True:
            b = resp.read(n)
            if not b:
                break
            yield b

    return _yield


class OtaClient:
    """GitHub based OTA client with ``stable`` and ``developer`` channels."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.owner = cfg.get("owner")
        self.repo = cfg.get("repo")
        self.stage_dir = STAGE_DIR
        self.backup_dir = BACKUP_DIR
        self.chunk = int(cfg.get("chunk", 1024))
        self.ensure_dirs(self.stage_dir)
        self.ensure_dirs(self.backup_dir)
        self._startup_cleanup()

    # ------------------------------------------------------------------
    # Connection helpers
    def connect(self) -> None:  # pragma: no cover - wifi not used in tests
        if network is None:
            return
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        if not sta.isconnected():
            sta.connect(self.cfg.get("ssid"), self.cfg.get("password"))
            for attempt in range(self.cfg.get("retries", 3)):
                if sta.isconnected():
                    break
                sleep(self.cfg.get("backoff_sec", 3))
        if not sta.isconnected():
            raise OTAError("WiFi connection failed")

    # ------------------------------------------------------------------
    # GitHub helpers
    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json"}
        token = self.cfg.get("token")
        if token:
            h["Authorization"] = "token {}".format(token)
        return h

    def _get(self, url: str, raw: bool = False):  # pragma: no cover - overridden in tests
        headers = self._headers()
        if raw:
            headers["Accept"] = "application/octet-stream"
        return requests.get(url, headers=headers, stream=raw)

    # ------------------------------------------------------------------
    def resolve_stable(self):
        """Return (tag, commit_sha) for the latest release."""

        url = "https://api.github.com/repos/%s/%s/releases/latest" % (self.owner, self.repo)
        r = self._get(url)
        try:
            j = r.json()
        finally:
            r.close()
        tag = j["tag_name"]
        commit = self._resolve_ref("tags/" + tag)
        return tag, commit

    def resolve_developer(self):
        """Return (branch, commit_sha) for the configured branch tip."""

        branch = self.cfg.get("branch", "main")
        url = "https://api.github.com/repos/%s/%s/git/ref/heads/%s" % (self.owner, self.repo, branch)
        r = self._get(url)
        try:
            j = r.json()
        finally:
            r.close()
        obj = j["object"]
        sha = obj["sha"]
        if obj.get("type") == "tag":
            sha = self._resolve_tag_object(sha)
        return branch, sha

    def _resolve_ref(self, ref_path: str) -> str:
        """Resolve a ref like ``tags/v1.0`` to a commit SHA."""

        url = "https://api.github.com/repos/%s/%s/git/ref/%s" % (self.owner, self.repo, ref_path)
        r = self._get(url)
        try:
            j = r.json()
        finally:
            r.close()
        obj = j["object"]
        if obj.get("type") == "commit":
            return obj["sha"]
        return self._resolve_tag_object(obj["sha"])

    def _resolve_tag_object(self, sha: str) -> str:
        url = "https://api.github.com/repos/%s/%s/git/tags/%s" % (self.owner, self.repo, sha)
        r = self._get(url)
        try:
            j = r.json()
        finally:
            r.close()
        return j["object"]["sha"]

    # ------------------------------------------------------------------
    def resolve_target(self):
        if self.cfg.get("channel") == "stable":
            tag, commit = self.resolve_stable()
            return {"ref": tag, "commit": commit, "mode": "tag"}
        branch, commit = self.resolve_developer()
        return {"ref": branch, "commit": commit, "mode": "branch"}

    # ------------------------------------------------------------------
    def fetch_tree(self, commit_sha):
        url = "https://api.github.com/repos/%s/%s/git/trees/%s?recursive=1" % (self.owner, self.repo, commit_sha)
        r = self._get(url)
        try:
            j = r.json()
        finally:
            r.close()
        return j["tree"]

    def iter_candidates(self, tree):
        allow = self.cfg.get("allow")
        ignore = self.cfg.get("ignore", [])
        for entry in tree:
            if entry.get("type") != "blob" or int(entry.get("size", 0)) == 0:
                continue
            path = entry["path"]
            if allow and not any(path == a or path.startswith(a.rstrip("/") + "/") for a in allow):
                continue
            if any(path == i or path.startswith(i.rstrip("/") + "/") for i in ignore):
                continue
            yield entry

    # ------------------------------------------------------------------
    def ensure_dirs(self, path: str) -> None:
        base = os.path.dirname(path)
        if not base:
            return
        parts = []
        while base and not os.path.isdir(base):
            parts.append(base)
            base = os.path.dirname(base)
        for p in reversed(parts):
            os.mkdir(p)

    def _startup_cleanup(self) -> None:
        """Rollback from backup and clean staging on boot."""
        if os.path.isdir(self.backup_dir) and os.listdir(self.backup_dir):
            for root, dirs, files in self._walk(self.backup_dir):
                for name in files:
                    bpath = os.path.join(root, name)
                    rel = bpath[len(self.backup_dir) + 1 :]
                    target = rel
                    self.ensure_dirs(target)
                    if os.path.exists(target):
                        os.remove(target)
                    os.rename(bpath, target)
            self._rmtree(self.backup_dir)
        if os.path.isdir(self.stage_dir) and os.listdir(self.stage_dir):
            self._rmtree(self.stage_dir)
        if not os.path.isdir(self.stage_dir):
            os.mkdir(self.stage_dir)
        if not os.path.isdir(self.backup_dir):
            os.mkdir(self.backup_dir)

    def stream_and_verify(self, entry, ref):
        """Download ``entry`` at ``ref`` to the staging directory."""

        path = entry["path"]
        size = int(entry.get("size", 0))
        if entry.get("type") != "blob" or size == 0:
            return True
        url = "https://raw.githubusercontent.com/%s/%s/%s/%s" % (self.owner, self.repo, ref, path)
        r = self._get(url, raw=True)
        try:
            tmp_path = self.stage_dir + "/" + path + ".tmp"
            self.ensure_dirs(tmp_path)
            f = open(tmp_path, "wb")
            try:
                def reader(n):
                    for chunk in http_reader(r)(n):
                        f.write(chunk)
                        yield chunk

                digest = git_blob_sha1_stream(size, reader, self.chunk)
            finally:
                f.close()
            if digest != entry["sha"]:
                raise OTAError("Hash mismatch for " + path)
            final_path = self.stage_dir + "/" + path
            self.ensure_dirs(final_path)
            try:
                os.remove(final_path)
            except OSError:
                pass
            os.rename(tmp_path, final_path)
        finally:
            r.close()
        return True

    # ------------------------------------------------------------------
    def stage_and_swap(self, applied_ref):
        """Atomically move staged files into place with rollback."""

        applied = []
        try:
            for root, dirs, files in self._walk(self.stage_dir):
                for name in files:
                    stage_path = os.path.join(root, name)
                    rel = stage_path[len(self.stage_dir) + 1 :]
                    target = rel
                    backup = os.path.join(self.backup_dir, rel)
                    self.ensure_dirs(backup)
                    self.ensure_dirs(target)
                    if os.path.exists(target):
                        os.rename(target, backup)
                    os.rename(stage_path, target)
                    applied.append((target, backup))
            self._write_state(applied_ref)
        except Exception:
            for target, backup in reversed(applied):
                try:
                    if os.path.exists(backup):
                        if os.path.exists(target):
                            os.remove(target)
                        os.rename(backup, target)
                except Exception:
                    pass
            raise
        finally:
            self._rmtree(self.stage_dir)
            self._rmtree(self.backup_dir)
            self.ensure_dirs(self.stage_dir)
            self.ensure_dirs(self.backup_dir)

    # ------------------------------------------------------------------
    def update_if_available(self):  # pragma: no cover - exercised in tests via pieces
        target = self.resolve_target()
        tree = self.fetch_tree(target["commit"])
        if not self.compute_change_needed(target["ref"]):
            print("No update required")
            return False
        ref = target["ref"] if target["mode"] == "tag" else target["commit"]
        for entry in self.iter_candidates(tree):
            self.stream_and_verify(entry, ref)
        self.stage_and_swap(target["ref"])
        machine.reset()
        return True

    # ------------------------------------------------------------------
    # State and utility helpers
    def compute_change_needed(self, target_ref: str) -> bool:
        try:
            with open(VERSION_FILE) as f:
                current = json.load(f).get("ref")
        except Exception:
            current = None
        return current != target_ref

    def _write_state(self, ref: str) -> None:
        tmp = VERSION_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"ref": ref}, f)
            f.flush()
            if hasattr(os, "fsync"):
                os.fsync(f.fileno())
        os.rename(tmp, VERSION_FILE)

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
                try:
                    os.remove(os.path.join(root, f))
                except OSError:
                    pass
            for d in dirs:
                try:
                    os.rmdir(os.path.join(root, d))
                except OSError:
                    pass
        try:
            if path not in ("", "."):
                os.rmdir(path)
        except OSError:
            pass

