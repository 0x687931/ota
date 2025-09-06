"""Unified OTA updater for MicroPython Pico W

Supports two channels:
- stable via GitHub Releases with optional signed manifest and deletes
- developer via a branch tip using Git tree and Git blob SHA1 verification

Features:
- streaming downloads with low RAM usage
- staging plus atomic swap with backup and rollback
- fsync before rename to reduce power loss corruption
- Wi Fi connect with bounded retries
- optional post update hook
"""

import json
import os
import sys
from time import sleep

# ------------------------------------------------------------
# Environment and shims

MICROPYTHON = sys.implementation.name == "micropython"

try:
    import ubinascii as binascii  # type: ignore
except Exception:
    import binascii  # type: ignore

if MICROPYTHON:
    import uhashlib as hashlib  # type: ignore
    import urequests as requests  # type: ignore
    import network  # type: ignore
    import machine  # type: ignore
else:  # pragma: no cover
    import hashlib  # type: ignore

    class _NoRequests:
        def get(self, *a, **k):
            raise RuntimeError("urequests not available")

    requests = _NoRequests()  # type: ignore
    network = None  # type: ignore

    class _Machine:
        def reset(self):
            pass

    machine = _Machine()  # type: ignore

# ------------------------------------------------------------
# Constants

CHUNK = 1024
VERSION_FILE = "version.json"
STAGE_DIR = ".ota_stage"
BACKUP_DIR = ".ota_backup"

# ------------------------------------------------------------
# Errors

class OTAError(Exception):
    pass

# ------------------------------------------------------------
# Small helpers

def _hexdigest(h):
    return h.hexdigest() if hasattr(h, "hexdigest") else binascii.hexlify(h.digest()).decode()

def _crc32_update(crc, block):
    try:
        return binascii.crc32(block, crc)
    except Exception:
        # bitwise fallback to keep behaviour consistent with binascii.crc32
        state = crc ^ 0xFFFFFFFF
        for b in block:
            state ^= b
            for _ in range(8):
                state = (state >> 1) ^ 0xEDB88320 if (state & 1) else (state >> 1)
        return (state ^ 0xFFFFFFFF) & 0xFFFFFFFF

def _hmac_sha256_hex(key_bytes, data_bytes):
    try:
        import hmac  # type: ignore
        return _hexdigest(hmac.new(key_bytes, data_bytes, hashlib.sha256))
    except Exception:
        block = 64
        if len(key_bytes) > block:
            key_bytes = hashlib.sha256(key_bytes).digest()
        key_bytes = key_bytes + b"\x00" * (block - len(key_bytes))
        o_key = bytes((kb ^ 0x5C) for kb in key_bytes)
        i_key = bytes((kb ^ 0x36) for kb in key_bytes)
        inner = hashlib.sha256(i_key + data_bytes).digest()
        outer = hashlib.sha256(o_key + inner).digest()
        return binascii.hexlify(outer).decode()

def sha256_file(path, chunk=CHUNK):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return _hexdigest(h)

def crc32_file(path, chunk=CHUNK):
    crc = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            crc = _crc32_update(crc, b)
    return crc & 0xFFFFFFFF

def git_blob_sha1_stream(total_size, reader, chunk):
    h = hashlib.sha1()
    h.update(b"blob " + str(total_size).encode() + b"\x00")
    remaining = total_size
    for data in reader(chunk):
        remaining -= len(data)
        h.update(data)
    if remaining != 0:
        raise OTAError("size mismatch during stream")
    return _hexdigest(h)

def http_reader(resp):
    def _yield(n):
        src = getattr(resp, "raw", None)
        if src is None or not hasattr(src, "read"):
            src = resp
        while True:
            b = src.read(n)
            if not b:
                break
            yield b
    return _yield

def _requests_supports_stream():
    func = requests.get
    try:
        import inspect  # type: ignore
        sig = inspect.signature(func)
        if "stream" in sig.parameters:
            return True
        for p in sig.parameters.values():
            if p.kind == inspect.Parameter.VAR_KEYWORD:
                return True
    except Exception:
        try:
            code = func.__code__  # type: ignore
            if "stream" in getattr(code, "co_varnames", ()):
                return True
        except Exception:
            pass
    return False

# ------------------------------------------------------------
# Filesystem utils

def ensure_dirs(path_):
    base = path_.rpartition("/")[0]
    parts = []
    while base and not _isdir(base):
        parts.append(base)
        base = base.rpartition("/")[0]
    for p in reversed(parts):
        try:
            os.mkdir(p)
        except OSError:
            pass
    try:
        os.mkdir(path_)
    except OSError:
        pass

def _isdir(p):
    try:
        return (os.stat(p)[0] & 0x4000) != 0 if MICROPYTHON else os.path.isdir(p)
    except OSError:
        return False

def _walk(base):
    try:
        names = os.listdir(base)
    except OSError:
        names = []
    dirs = []
    files = []
    for name in names:
        p = base + "/" + name if base else name
        if _isdir(p):
            dirs.append(name)
        else:
            files.append(name)
    yield base, dirs, files
    for d in dirs:
        sub = (base + "/" + d) if base else d
        for x in _walk(sub):
            yield x

def _rmtree(path_):
    if not path_ or not _exists(path_):
        return
    for root, dirs, files in _walk(path_):
        for f in files:
            try:
                os.remove((root + "/" + f) if root else f)
            except OSError:
                pass
        for d in dirs:
            try:
                os.rmdir((root + "/" + d) if root else d)
            except OSError:
                pass
    try:
        if path_ not in (".", ""):
            os.rmdir(path_)
    except OSError:
        pass

def _exists(p):
    try:
        os.stat(p)
        return True
    except OSError:
        return False

# ------------------------------------------------------------
# Main class

class OTA:
    """Unified OTA client

    cfg keys:
      ssid, password
      owner, repo
      channel stable or developer
      branch main by default
      token optional for private repos
      user_agent optional
      http_timeout_sec int default 10
      connect_timeout_sec optional
      retries int default 5
      backoff_sec int default 3
      allow list of allowed path prefixes for developer channel
      ignore list of ignored path prefixes for developer channel
      manifest_key optional shared secret for signed manifest
      delete_patterns optional patterns to clean stale files in developer channel
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.stage = STAGE_DIR
        self.backup = BACKUP_DIR
        ensure_dirs(self.stage)
        ensure_dirs(self.backup)
        self._startup_cleanup()

    # --------------------------------------------------------
    # Boot safety

    def _startup_cleanup(self):
        if _isdir(self.backup) and os.listdir(self.backup):
            for root, dirs, files in _walk(self.backup):
                for name in files:
                    bpath = (root + "/" + name)
                    rel = bpath[len(self.backup) + 1 :]
                    target = rel
                    ensure_dirs(rel.rpartition("/")[0])
                    if _exists(target):
                        try:
                            os.remove(target)
                        except OSError:
                            pass
                    os.rename(bpath, target)
            _rmtree(self.backup)
        if _isdir(self.stage) and os.listdir(self.stage):
            _rmtree(self.stage)
        ensure_dirs(self.stage)
        ensure_dirs(self.backup)

    # --------------------------------------------------------
    # Network

    def connect(self):
        if network is None:
            return
        ssid = self.cfg.get("ssid")
        if not ssid:
            raise OTAError("Wi Fi SSID not configured")
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        if not sta.isconnected():
            sta.connect(ssid, self.cfg.get("password"))
            attempts = 0
            retries = int(self.cfg.get("retries", 5))
            backoff = int(self.cfg.get("backoff_sec", 3))
            while not sta.isconnected() and attempts < retries:
                sleep(backoff)
                attempts += 1
        if not sta.isconnected():
            raise OTAError("Wi Fi connection failed")

    def _headers(self):
        h = {"Accept": "application/vnd.github+json"}
        token = self.cfg.get("token")
        if token:
            h["Authorization"] = "token {}".format(token)
        h["User-Agent"] = self.cfg.get("user_agent", "ota-updater")
        return h

    def _get(self, url: str, raw: bool = False):
        headers = self._headers()
        if raw:
            headers["Accept"] = "application/octet-stream"
        connect_timeout = self.cfg.get("connect_timeout_sec")
        read_timeout = self.cfg.get("http_timeout_sec", 10)
        timeout = None
        if connect_timeout is not None and read_timeout is not None:
            timeout = (max(connect_timeout, read_timeout) if MICROPYTHON else (connect_timeout, read_timeout))
        elif connect_timeout is not None:
            timeout = connect_timeout
        elif read_timeout is not None:
            timeout = read_timeout
        kwargs = {"headers": headers}
        if _requests_supports_stream():
            kwargs["stream"] = raw
        if timeout is not None:
            kwargs["timeout"] = timeout
        r = requests.get(url, **kwargs)
        status = getattr(r, "status_code", 200)
        if status >= 400:
            try:
                body = getattr(r, "text", "")
            except Exception:
                body = ""
            try:
                r.close()
            except Exception:
                pass
            raise OTAError("HTTP {} {}".format(status, (body[:80] if isinstance(body, str) else "")))
        return r

    def _get_json(self, url: str):
        r = self._get(url, raw=False)
        try:
            return r.json()
        finally:
            try:
                r.close()
            except Exception:
                pass

    # --------------------------------------------------------
    # Target resolution

    def _resolve_ref(self, ref_path: str) -> str:
        j = self._get_json("https://api.github.com/repos/%s/%s/git/ref/%s" %
                           (self.cfg["owner"], self.cfg["repo"], ref_path))
        obj = j["object"]
        if obj.get("type") == "commit":
            return obj["sha"]
        tag_obj = self._get_json("https://api.github.com/repos/%s/%s/git/tags/%s" %
                                 (self.cfg["owner"], self.cfg["repo"], obj["sha"]))
        return tag_obj["object"]["sha"]

    def _resolve_stable(self):
        url = "https://api.github.com/repos/%s/%s/releases/latest" % (self.cfg["owner"], self.cfg["repo"])
        j = self._get_json(url)
        tag = j["tag_name"]
        commit = self._resolve_ref("tags/" + tag)
        return {"ref": tag, "commit": commit, "mode": "tag", "release_json": j}

    def _resolve_developer(self):
        branch = self.cfg.get("branch", "main")
        url = "https://api.github.com/repos/%s/%s/git/ref/heads/%s" % (self.cfg["owner"], self.cfg["repo"], branch)
        j = self._get_json(url)
        obj = j["object"]
        sha = obj["sha"]
        if obj.get("type") == "tag":
            sha = self._get_json("https://api.github.com/repos/%s/%s/git/tags/%s" %
                                 (self.cfg["owner"], self.cfg["repo"], sha))["object"]["sha"]
        return {"ref": branch, "commit": sha, "mode": "branch", "release_json": None}

    def resolve_target(self):
        ch = self.cfg.get("channel", "stable")
        if ch == "stable":
            return self._resolve_stable()
        return self._resolve_developer()

    # --------------------------------------------------------
    # Tree and candidates

    def fetch_tree(self, commit_sha):
        url = "https://api.github.com/repos/%s/%s/git/trees/%s?recursive=1" % (
            self.cfg["owner"], self.cfg["repo"], commit_sha
        )
        return self._get_json(url)["tree"]

    def iter_candidates(self, tree):
        allow = self.cfg.get("allow")
        ignore = self.cfg.get("ignore", [])
        for entry in tree:
            if entry.get("type") != "blob" or int(entry.get("size", 0)) == 0:
                continue
            p = entry["path"]
            if allow and not any(p == a or p.startswith(a.rstrip("/") + "/") for a in allow):
                continue
            if any(p == i or p.startswith(i.rstrip("/") + "/") for i in ignore):
                continue
            yield entry

    # --------------------------------------------------------
    # Streaming and staging

    def _stage_path(self, rel):
        return self.stage + "/" + rel

    def _backup_path(self, rel):
        return self.backup + "/" + rel

    def stream_and_verify_git(self, entry, ref):
        rel = entry["path"]
        size = int(entry.get("size", 0))
        if size == 0 or entry.get("type") != "blob":
            return
        url = "https://raw.githubusercontent.com/%s/%s/%s/%s" % (
            self.cfg["owner"], self.cfg["repo"], ref, rel
        )
        r = self._get(url, raw=True)
        try:
            tmp = self._stage_path(rel) + ".tmp"
            ensure_dirs(tmp.rpartition("/")[0])
            f = open(tmp, "wb")
            try:
                def reader(n):
                    for chunk in http_reader(r)(n):
                        f.write(chunk)
                        yield chunk
                digest = git_blob_sha1_stream(size, reader, CHUNK)
                f.flush()
                if hasattr(os, "fsync"):
                    os.fsync(f.fileno())
            finally:
                f.close()
            if digest != entry["sha"]:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
                raise OTAError("hash mismatch for " + rel)
            final_ = self._stage_path(rel)
            ensure_dirs(final_.rpartition("/")[0])
            try:
                os.remove(final_)
            except OSError:
                pass
            os.rename(tmp, final_)
        finally:
            try:
                r.close()
            except Exception:
                pass

    def _download_asset(self, url, dest, expected_sha=None, expected_crc=None, expected_size=None):
        r = self._get(url, raw=True)
        tmp = dest + ".tmp"
        h = hashlib.sha256()
        crc = 0
        total = 0
        with open(tmp, "wb") as f:
            for block in http_reader(r)(CHUNK):
                total += len(block)
                h.update(block)
                crc = _crc32_update(crc, block)
                f.write(block)
            f.flush()
            if hasattr(os, "fsync"):
                os.fsync(f.fileno())
        try:
            r.close()
        except Exception:
            pass
        if expected_size is not None and total != expected_size:
            os.remove(tmp)
            raise OTAError("size mismatch for {}".format(dest))
        sha = _hexdigest(h)
        crc &= 0xFFFFFFFF
        if expected_sha and sha != expected_sha:
            os.remove(tmp)
            raise OTAError("sha256 mismatch for {}".format(dest))
        if not expected_sha and expected_crc is not None and crc != expected_crc:
            os.remove(tmp)
            raise OTAError("crc32 mismatch for {}".format(dest))
        os.rename(tmp, dest)

    # --------------------------------------------------------
    # Swap with rollback

    def stage_and_swap(self, applied_ref, deletes=None, safe_tail=None):
        applied = []
        try:
            # move staged files into place
            for root, dirs, files in _walk(self.stage):
                for name in files:
                    stage_path = (root + "/" + name)
                    rel = stage_path[len(self.stage) + 1 :]
                    target = rel
                    backup = self._backup_path(rel)
                    ensure_dirs(backup.rpartition("/")[0])
                    ensure_dirs(target.rpartition("/")[0])
                    if _exists(target):
                        os.rename(target, backup)
                    os.rename(stage_path, target)
                    applied.append((target, backup))
            # deletions from manifest
            if deletes:
                for rel in deletes:
                    if _exists(rel):
                        bpath = self._backup_path(rel)
                        ensure_dirs(bpath.rpartition("/")[0])
                        os.rename(rel, bpath)
                        applied.append((None, bpath))
            # optional conservative deletion for developer channel
            patterns = self.cfg.get("delete_patterns", [])
            if patterns:
                staged_now = set()
                for root, dirs, files in _walk(self.stage):
                    for n in files:
                        staged_now.add((root + "/" + n)[len(self.stage) + 1 :])
                for root, dirs, files in _walk(""):
                    if root.startswith(STAGE_DIR) or root.startswith(BACKUP_DIR):
                        continue
                    for n in files:
                        rel = (root + "/" + n) if root else n
                        if rel == VERSION_FILE:
                            continue
                        if any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in patterns):
                            if rel not in staged_now and _exists(rel):
                                bpath = self._backup_path(rel)
                                ensure_dirs(bpath.rpartition("/")[0])
                                try:
                                    os.rename(rel, bpath)
                                    applied.append((None, bpath))
                                except Exception:
                                    pass
            self._write_state(applied_ref)
        except Exception:
            # best effort rollback
            for target, backup in reversed(applied):
                try:
                    if backup and _exists(backup):
                        if target and _exists(target):
                            os.remove(target)
                        os.rename(backup, target or backup)
                except Exception:
                    pass
            raise
        finally:
            _rmtree(self.stage)
            _rmtree(self.backup)
            ensure_dirs(self.stage)
            ensure_dirs(self.backup)

    def _write_state(self, ref: str):
        tmp = VERSION_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"ref": ref}, f)
            f.flush()
            if hasattr(os, "fsync"):
                os.fsync(f.fileno())
        os.rename(tmp, VERSION_FILE)

    def _read_state(self):
        try:
            with open(VERSION_FILE) as f:
                return json.load(f).get("ref")
        except Exception:
            return None

    # --------------------------------------------------------
    # Signed manifest path for stable release

    def _verify_manifest_signature(self, manifest: dict):
        key = self.cfg.get("manifest_key")
        if not key:
            return
        sig = manifest.get("signature")
        if not sig:
            raise OTAError("manifest missing signature")
        tmp = manifest.copy()
        tmp.pop("signature", None)
        data = json.dumps(tmp, sort_keys=True, separators=(",", ":")).encode()
        expected = _hmac_sha256_hex(key.encode(), data)
        try:
            import hmac as _h  # type: ignore
            ok = _h.compare_digest(expected, sig)
        except Exception:
            ok = expected == sig
        if not ok:
            raise OTAError("manifest signature mismatch")

    def _stable_with_manifest(self, rel_json, tag):
        # find manifest asset
        asset = None
        for a in rel_json.get("assets", []):
            if a.get("name") == "manifest.json":
                asset = a
                break
        if not asset:
            return None
        # download manifest
        url = asset["url"]
        r = self._get(url, raw=True)
        try:
            manifest = r.json()
        finally:
            try:
                r.close()
            except Exception:
                pass
        self._verify_manifest_signature(manifest)
        current = self._read_state()
        version = manifest.get("version", tag)
        if current == version:
            return {"updated": False}
        # stage all files listed with raw URLs at this tag
        for fi in manifest.get("files", []):
            rel = fi["path"]
            raw_url = "https://raw.githubusercontent.com/%s/%s/%s/%s" % (
                self.cfg["owner"], self.cfg["repo"], tag, rel
            )
            dest = self._stage_path(rel)
            ensure_dirs(dest.rpartition("/")[0])
            self._download_asset(
                raw_url,
                dest,
                expected_sha=fi.get("sha256"),
                expected_crc=fi.get("crc32"),
                expected_size=fi.get("size"),
            )
            # verify again from disk to be safe
            if fi.get("sha256"):
                if sha256_file(dest) != fi["sha256"]:
                    raise OTAError("sha256 mismatch after write for " + rel)
            elif fi.get("crc32") is not None:
                if crc32_file(dest) != int(fi["crc32"]):
                    raise OTAError("crc32 mismatch after write for " + rel)
        # swap and optional deletes
        self.stage_and_swap(version, deletes=manifest.get("deletes", []))
        # optional post update hook
        hook = manifest.get("post_update")
        if hook:
            self._run_hook(hook)
        return {"updated": True}

    # --------------------------------------------------------
    # Public entry point

    def update_if_available(self):
        self.connect()
        target = self.resolve_target()
        if target["mode"] == "tag":
            # try manifest path first
            res = self._stable_with_manifest(target["release_json"], target["ref"])
            if res is not None:
                if res.get("updated"):
                    machine.reset()
                    return True
                print("No update required")
                return False
        # developer path or stable without manifest
        if self._read_state() == target["ref"] and target["mode"] == "tag":
            print("No update required")
            return False
        tree = self.fetch_tree(target["commit"])
        ref_for_download = target["ref"] if target["mode"] == "tag" else target["commit"]
        for entry in self.iter_candidates(tree):
            self.stream_and_verify_git(entry, ref_for_download)
        self.stage_and_swap(target["ref"])
        machine.reset()
        return True

    # --------------------------------------------------------
    # Hook

    def _run_hook(self, path_):
        try:
            mod = path_.replace("/", ".")
            if mod.endswith(".py"):
                mod = mod[:-3]
            __import__(mod)
        except Exception as exc:
            print("post update hook failed:", exc)
