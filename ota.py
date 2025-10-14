"""
Unified OTA updater for MicroPython Pico W with memory and reliability improvements
"""

import os
import sys
from time import sleep

# ------------------------------------------------------------
# JSON with MicroPython preference
try:
    import ujson as json  # type: ignore
except Exception:
    import json  # type: ignore

# const helper
try:
    from micropython import const  # type: ignore
except Exception:
    def const(x):  # type: ignore
        return x

MICROPYTHON = sys.implementation.name == "micropython"

# ------------------------------------------------------------
# Binary helpers
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

        def soft_reset(self):
            pass

    machine = _Machine()  # type: ignore

# ------------------------------------------------------------
# Constants

CHUNK = const(1024)
VERSION_FILE = "version.json"
ERROR_FILE = "ota_error.json"
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

# fast CRC32 fallback with table for ports that lack binascii.crc32
_CRC32_TAB = None
def _crc32_update(crc, block):
    try:
        return binascii.crc32(block, crc)
    except Exception:
        global _CRC32_TAB
        if _CRC32_TAB is None:
            tab = []
            for i in range(256):
                c = i
                for _ in range(8):
                    c = (c >> 1) ^ 0xEDB88320 if (c & 1) else (c >> 1)
                tab.append(c & 0xFFFFFFFF)
            _CRC32_TAB = tuple(tab)
        c = crc ^ 0xFFFFFFFF
        for b in block:
            c = _CRC32_TAB[(c ^ b) & 0xFF] ^ (c >> 8)
        return (c ^ 0xFFFFFFFF) & 0xFFFFFFFF

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
    buf = bytearray(chunk)
    mv = memoryview(buf)
    with open(path, "rb") as f:
        readinto = getattr(f, "readinto", None)
        if readinto:
            while True:
                n = readinto(buf)
                if not n:
                    break
                h.update(mv[:n])
        else:
            while True:
                b = f.read(chunk)
                if not b:
                    break
                h.update(b)
    return _hexdigest(h)

def crc32_file(path, chunk=CHUNK):
    crc = 0
    buf = bytearray(chunk)
    mv = memoryview(buf)
    with open(path, "rb") as f:
        readinto = getattr(f, "readinto", None)
        if readinto:
            while True:
                n = readinto(buf)
                if not n:
                    break
                crc = _crc32_update(crc, mv[:n])
        else:
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
    func = getattr(requests, "get", None)
    if func is None or getattr(func, "__module__", "") == "urequests":
        return False
    try:
        code = func.__code__
        return "stream" in code.co_varnames[: code.co_argcount]
    except Exception:
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
    """Unified OTA client"""

    def _init_adapt_state(self):
        self._adaptations = {
            "mem_chunk": None,      # new chunk size if lowered
            "net_retries": None,    # new retries if raised
            "net_backoff": None,    # new backoff if raised
            "wifi_pm": None,        # applied power mode tweak
        }

    def _init_watchdog(self):
        """Initialize hardware watchdog timer for headless operation recovery."""
        self._wdt = None
        if not MICROPYTHON:
            return

        wdt_timeout = self.cfg.get("watchdog_timeout_ms")
        if not wdt_timeout:
            return

        try:
            from machine import WDT
            self._wdt = WDT(timeout=wdt_timeout)
            self._debug("Watchdog initialized with {}ms timeout".format(wdt_timeout))
        except Exception as e:
            self._debug("Watchdog not available:", e)

    def _feed_watchdog(self):
        """Feed the watchdog timer to prevent reset."""
        if self._wdt:
            try:
                self._wdt.feed()
            except Exception:
                pass

    def _init_led(self):
        """Initialize status LED for visual feedback."""
        self._led = None
        if not MICROPYTHON:
            return

        led_pin = self.cfg.get("status_led_pin")
        if led_pin is None:
            return

        try:
            from machine import Pin
            self._led = Pin(led_pin, Pin.OUT)
            self._led.value(0)  # Start with LED off
            self._debug("Status LED initialized on pin {}".format(led_pin))
        except Exception as e:
            self._debug("Status LED not available:", e)

    def _led_blink(self, pattern):
        """Blink LED with pattern: list of (on_duration_ms, off_duration_ms) tuples."""
        if not self._led:
            return
        try:
            from time import sleep_ms
            for on_ms, off_ms in pattern:
                self._led.value(1)
                # Feed watchdog during sleep to prevent reset
                elapsed = 0
                while elapsed < on_ms:
                    sleep_ms(min(100, on_ms - elapsed))
                    elapsed += 100
                    self._feed_watchdog()

                self._led.value(0)
                if off_ms > 0:
                    elapsed = 0
                    while elapsed < off_ms:
                        sleep_ms(min(100, off_ms - elapsed))
                        elapsed += 100
                        self._feed_watchdog()
        except Exception:
            pass

    def _led_set(self, state):
        """Set LED to on (1) or off (0)."""
        if self._led:
            try:
                self._led.value(state)
            except Exception:
                pass

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.stage = cfg.get("stage_dir", STAGE_DIR)
        self.backup = cfg.get("backup_dir", BACKUP_DIR)
        ensure_dirs(self.stage)
        ensure_dirs(self.backup)
        self.chunk = int(cfg.get("chunk", CHUNK))
        allow = cfg.get("allow") or []
        allow = [a.strip("/") for a in allow if a]
        self._allow = tuple(allow) or None
        self._allow_prefixes = tuple(a + "/" for a in allow) if allow else None
        ignore = cfg.get("ignore") or []
        ignore = [i.strip("/") for i in ignore if i]
        self._ignore = tuple(ignore) or None
        self._ignore_prefixes = tuple(i + "/" for i in ignore) if ignore else None
        self._init_adapt_state()
        self._init_watchdog()
        self._init_led()
        self._startup_cleanup()
        # Trace active filters once per run
        self._debug("Filter allow:", self._allow)
        self._debug("Filter ignore:", self._ignore)

    def _debug(self, *args):
        if self.cfg.get("debug"):
            print("[OTA]", *args)

    def _info(self, *args):
        # Only show plain messages when not in debug mode
        if not self.cfg.get("debug"):
            print(*args)

    def _format_version(self, state: dict) -> str:
        """Format version info for debug output.

        Shortens the commit hash and combines it with the ref when available.
        """
        if not state:
            return ""
        ref = state.get("ref")
        commit = state.get("commit")
        short = commit[:7] if commit else None
        if ref and short:
            return f"{ref} #{short}"
        if ref:
            return ref
        if short:
            return f"#{short}"
        return ""

    # --------------------------------------------------------
    # Path filtering

    def _is_permitted(self, path: str) -> bool:
        # normalise before matching
        path = self._normalize_path(path)
        if self._allow:
            if path not in self._allow:
                if not self._allow_prefixes or not any(path.startswith(p) for p in self._allow_prefixes):
                    return False
        if self._ignore:
            if path in self._ignore:
                return False
            if self._ignore_prefixes and any(path.startswith(p) for p in self._ignore_prefixes):
                return False
        return True

    # --------------------------------------------------------
    # Resource checks

    def _cpu_mhz(self):
        if MICROPYTHON:
            try:
                return int(machine.freq() // 1000000)
            except Exception:
                return None
        try:  # pragma: no cover
            import psutil  # type: ignore
            freq = getattr(psutil.cpu_freq(), "current", None)
            return int(freq) if freq else None
        except Exception:  # pragma: no cover
            return None

    def _mem_free(self):
        try:
            import gc
            return gc.mem_free()  # type: ignore
        except Exception:
            try:  # pragma: no cover
                import psutil  # type: ignore
                return int(psutil.virtual_memory().available)
            except Exception:
                return None

    def _storage_free(self):
        try:
            st = os.statvfs(".")
            try:
                return st.f_bavail * st.f_frsize
            except AttributeError:
                return st[4] * st[1]
        except Exception:
            return None

    def _battery_level(self):
        """Get battery level percentage if available."""
        if not MICROPYTHON:
            return None
        try:
            from machine import ADC, Pin
            # Check for configured battery ADC pin
            battery_pin = self.cfg.get("battery_adc_pin")
            if battery_pin is None:
                return None

            adc = ADC(Pin(battery_pin))
            # Read voltage (typical range 0-3.3V on Pico)
            raw = adc.read_u16()
            voltage = raw * 3.3 / 65535

            # Apply voltage divider ratio if configured
            divider_ratio = self.cfg.get("battery_divider_ratio", 1.0)
            battery_voltage = voltage * divider_ratio

            # Convert to percentage (typical LiPo: 4.2V full, 3.0V empty)
            v_max = self.cfg.get("battery_v_max", 4.2)
            v_min = self.cfg.get("battery_v_min", 3.0)
            percentage = ((battery_voltage - v_min) / (v_max - v_min)) * 100
            return max(0, min(100, percentage))
        except Exception:
            return None

    def _check_basic_resources(self):
        free_mem = self._mem_free()
        if free_mem is not None:
            old_chunk = self.chunk
            # cap chunk size and adapt downwards on low memory
            self.chunk = max(256, min(self.chunk, free_mem // 4, 4096))
            if self.chunk != old_chunk:
                self._adaptations["mem_chunk"] = self.chunk
            min_mem = int(self.cfg.get("min_free_mem", 0))
            if free_mem < min_mem:
                return False
        min_cpu = self.cfg.get("min_cpu_mhz")
        if min_cpu is not None:
            cpu = self._cpu_mhz()
            if cpu is not None and cpu < min_cpu:
                return False
        # Check battery level for battery-powered devices
        min_battery = self.cfg.get("min_battery_percent")
        if min_battery is not None:
            battery = self._battery_level()
            if battery is not None and battery < min_battery:
                return False
        return True

    def _check_storage(self, required):
        free = self._storage_free()
        min_storage = int(self.cfg.get("min_free_storage", 0))
        need = max(required, min_storage)
        if free is not None and free < need:
            return False
        return True

    def _validate_update_plan(self, candidates):
        """Pre-download validation to catch issues early."""
        if not candidates:
            self._debug("Validation: No files to update")
            return True  # Empty update is valid (e.g., when force=True but no changes)

        # Validate paths
        for entry in candidates:
            path = entry.get("path", "")
            try:
                self._normalize_path(path)
            except OTAError as e:
                self._debug("Validation failed: Invalid path", path, str(e))
                return False

            # Check if file is permitted
            if not self._is_permitted(path):
                self._debug("Validation failed: Path not permitted", path)
                return False

        # Check total size requirements
        total_size = sum(int(entry.get("size", 0)) for entry in candidates)
        if not self._check_storage(total_size * 2):
            self._debug("Validation failed: Insufficient storage for {} bytes".format(total_size))
            return False

        self._debug("Validation: {} files, {:.2f} KB total".format(
            len(candidates), total_size / 1024))
        return True

    # --------------------------------------------------------
    # Boot safety

    def _startup_cleanup(self):
        # Check if backup dir has files to restore
        try:
            has_backup_files = _isdir(self.backup) and os.listdir(self.backup)
        except OSError:
            has_backup_files = False

        if has_backup_files:
            # Phase 1: Build restoration plan and validate all operations
            restore_plan = []
            for root, dirs, files in _walk(self.backup):
                for name in files:
                    bpath = (root + "/" + name)
                    rel = bpath[len(self.backup) + 1 :]
                    if not self._is_permitted(rel):
                        continue
                    target = rel
                    restore_plan.append((bpath, target))

            # Phase 2: Execute all restores atomically
            for bpath, target in restore_plan:
                ensure_dirs(target.rpartition("/")[0])
                if _exists(target):
                    try:
                        os.remove(target)
                    except OSError:
                        pass
                try:
                    os.rename(bpath, target)
                    # Sync immediately after each restore for safety
                    if hasattr(os, "sync"):
                        try:
                            os.sync()
                        except Exception:
                            pass
                except OSError as e:
                    # Critical: restoration failed after validation
                    self._debug("Critical: restore failed for", target, ":", e)
                    self._write_error_state(["Restore failed: {}: {}".format(target, str(e))])
                    # Continue with remaining restores rather than abort completely
                    continue
            _rmtree(self.backup)

        # Check if stage dir needs cleanup (includes orphaned .tmp files)
        try:
            has_stage_files = _isdir(self.stage) and os.listdir(self.stage)
        except OSError:
            has_stage_files = False

        if has_stage_files:
            # Clean up orphaned .tmp files from failed downloads
            for root, dirs, files in _walk(self.stage):
                for name in files:
                    tmp_path = (root + "/" + name) if root else name
                    if name.endswith('.tmp'):
                        try:
                            os.remove(tmp_path)
                            self._debug("Removed orphaned temp file:", tmp_path)
                        except OSError:
                            pass
            _rmtree(self.stage)
        ensure_dirs(self.stage)
        ensure_dirs(self.backup)

    # --------------------------------------------------------
    # Network

    def connect(self):
        """Connect using best available transport (WiFi, Cellular, LoRa)."""
        # Check if multi-connectivity is enabled
        if self.cfg.get("cellular_enabled") or self.cfg.get("lora_enabled"):
            # Use multi-connectivity manager
            try:
                from connectivity import ConnectivityManager
            except ImportError:
                self._debug("connectivity.py not found, falling back to WiFi-only")
                return self._connect_wifi_only()

            if not hasattr(self, "_conn_mgr"):
                self._conn_mgr = ConnectivityManager(self.cfg)

            try:
                # Signal connection attempt
                self._led_blink([(100, 100), (100, 100)])
                name, transport = self._conn_mgr.connect_best_available()
                self._active_transport = transport
                self._active_transport_name = name

                # Signal success
                self._led_set(1)
                self._debug("Connected via", name)

                # Show signal quality if available
                signal = transport.get_signal_strength()
                if signal is not None:
                    self._debug("Signal strength:", "{}%".format(signal))

                return
            except Exception as e:
                # Signal failure
                self._led_blink([(500, 0)])
                raise OTAError("All connectivity options failed: {}".format(str(e)))
        else:
            # WiFi-only mode (original implementation)
            return self._connect_wifi_only()

    def _connect_wifi_only(self):
        """Original WiFi-only connection logic."""
        if network is None:
            return
        # Signal WiFi connection attempt
        self._led_blink([(100, 100), (100, 100)])
        ssid = self.cfg.get("ssid")
        if not ssid:
            raise OTAError("Wi Fi SSID not configured")
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        try:
            sta.config(pm=0xA11140)
            self._adaptations["wifi_pm"] = "pm=0xA11140"
        except Exception:
            pass
        if not sta.isconnected():
            password = self.cfg.get("password")
            # Handle None or empty password - some implementations require explicit empty string
            if password:
                sta.connect(ssid, password)
            else:
                sta.connect(ssid, "")
            retries = int(self.cfg.get("retries", 5))
            initial_backoff = int(self.cfg.get("backoff_sec", 3))
            # if RSSI is poor we adapt retries and backoff on the fly
            try:
                rssi = sta.status("rssi")
                if isinstance(rssi, int):
                    if rssi < -75:
                        # poor link: more retries and longer backoff
                        if retries < 8:
                            retries = 8
                            self._adaptations["net_retries"] = retries
                        if initial_backoff < 5:
                            initial_backoff = 5
                            self._adaptations["net_backoff"] = initial_backoff
                    elif rssi < -70:
                        # fair link: mild bump
                        if retries < 6:
                            retries = 6
                            self._adaptations["net_retries"] = retries
                # if RSSI unknown, leave defaults
            except Exception:
                pass

            # Exponential backoff with max cap to avoid excessive delays
            backoff = initial_backoff
            max_backoff = int(self.cfg.get("max_backoff_sec", 60))
            for attempt in range(retries):
                if sta.isconnected():
                    break
                status = getattr(sta, "status", lambda: 0)()
                if isinstance(status, int) and status < 0:
                    break
                if attempt > 0:
                    sleep(backoff)
                    # Exponential backoff: double the delay each time, up to max_backoff
                    backoff = min(backoff * 2, max_backoff)
        if not sta.isconnected():
            # Signal connection failure with long blink
            self._led_blink([(500, 0)])
            raise OTAError("Wi Fi connection failed")
        # Signal successful connection with solid LED
        self._led_set(1)
        self._debug("Connected to Wi Fi:", sta.ifconfig()[0])

    def _get_active_transport_info(self):
        """Get information about active transport."""
        if hasattr(self, "_active_transport") and hasattr(self, "_active_transport_name"):
            return {
                "name": self._active_transport_name,
                "bandwidth": self._active_transport.get_bandwidth(),
                "cost_per_kb": self._active_transport.get_cost_per_kb(),
                "signal": self._active_transport.get_signal_strength()
            }
        return None

    def _estimate_update_cost(self, total_bytes):
        """Estimate cost of update in USD based on active transport."""
        transport_info = self._get_active_transport_info()
        if transport_info and transport_info["cost_per_kb"] > 0:
            cost = (total_bytes / 1024) * transport_info["cost_per_kb"]
            self._debug("Estimated update cost: ${:.2f}".format(cost))
            return cost
        return 0.0

    def _should_prefer_delta(self):
        """Determine if delta updates should be preferred based on active transport."""
        transport_info = self._get_active_transport_info()
        if transport_info:
            bandwidth = transport_info["bandwidth"]
            cost_per_kb = transport_info["cost_per_kb"]
            # Prefer delta for low bandwidth or costly connections
            if bandwidth in ("low", "very_low") or cost_per_kb > 0:
                return True
        return self.cfg.get("enable_delta_updates", False)

    def _debug_resources(self):
        if not self.cfg.get("debug"):
            return
        cpu = self._cpu_mhz()
        if cpu is not None:
            self._debug("CPU MHz:", cpu)

        mem = self._mem_free()
        if mem is not None:
            self._debug("Free memory: {:.1f} KB".format(mem / 1024))

        st = self._storage_free()
        if st is not None:
            self._debug("Free storage: {:.2f} MB".format(st / (1024 * 1024)))

        battery = self._battery_level()
        if battery is not None:
            self._debug("Battery level: {:.1f}%".format(battery))

        # Show transport info if multi-connectivity is active
        transport_info = self._get_active_transport_info()
        if transport_info:
            self._debug("Transport:", transport_info["name"])
            self._debug("Bandwidth:", transport_info["bandwidth"])
            if transport_info["cost_per_kb"] > 0:
                self._debug("Cost per KB: ${:.4f}".format(transport_info["cost_per_kb"]))

        if network is not None:
            try:
                sta = network.WLAN(network.STA_IF)
                if sta.isconnected():
                    ip, mask, gw, dns = sta.ifconfig()
                    self._debug("Wi Fi SSID:", self.cfg.get("ssid"))
                    self._debug("Wi Fi IP:", ip)
                    try:
                        rssi = sta.status("rssi")
                        if isinstance(rssi, int):
                            if rssi >= -55:
                                quality = "good"
                            elif rssi >= -70:
                                quality = "fair"
                            else:
                                quality = "poor"
                            self._debug("Wi Fi RSSI:", "{} dBm ({})".format(rssi, quality))
                    except Exception:
                        pass
            except Exception as exc:
                self._debug("Wi Fi status unavailable:", exc)

        # Only print adjustment notes if we actually adapted behaviour
        a = self._adaptations
        if a["mem_chunk"] is not None:
            self._debug("Adapt: chunk size ->", a["mem_chunk"])
        if a["net_retries"] is not None:
            self._debug("Adapt: connect retries ->", a["net_retries"])
        if a["net_backoff"] is not None:
            self._debug("Adapt: connect backoff ->", a["net_backoff"])
        if a["wifi_pm"] is not None:
            self._debug("Adapt: wifi power mode ->", a["wifi_pm"])

    def _headers(self):
        h = {"Accept": "application/vnd.github+json"}
        token = self.cfg.get("token")
        if token:
            h["Authorization"] = "token {}".format(token)
        h["User-Agent"] = self.cfg.get("user_agent", "ota-updater")
        return h

    def _get(self, url: str, raw: bool = False):
        """GET with retry logic for transient failures."""
        max_retries = int(self.cfg.get("http_retries", 3))
        backoff = int(self.cfg.get("backoff_sec", 3))
        max_backoff = int(self.cfg.get("max_backoff_sec", 60))

        last_error = None
        for attempt in range(max_retries):
            try:
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
                self._debug("GET", url)
                r = requests.get(url, **kwargs)
                status = getattr(r, "status_code", 200)
                if status >= 400:
                    # keep error strings short and robust on MicroPython
                    try:
                        r.close()
                    except Exception:
                        pass
                    raise OTAError("HTTP {}".format(status))
                return r
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    sleep_time = min(backoff * (2 ** attempt), max_backoff)
                    self._debug("Request failed, retrying in {}s: {}".format(sleep_time, str(e)))
                    for _ in range(sleep_time):
                        sleep(1)
                        self._feed_watchdog()
                else:
                    self._debug("All retry attempts exhausted")

        raise last_error

    def _get_json(self, url: str):
        # Preemptive GC before large JSON allocation to prevent fragmentation
        import gc
        gc.collect()

        r = self._get(url, raw=False)
        try:
            data = r.json()
            # Collect any parsing overhead immediately
            gc.collect()
            return data
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
        for entry in tree:
            if entry.get("type") != "blob" or int(entry.get("size", 0)) == 0:
                continue
            p = entry["path"]
            if not self._is_permitted(p):
                continue
            yield entry

    # --------------------------------------------------------
    # Streaming and staging

    def _normalize_path(self, rel):
        if rel.startswith("/"):
            raise OTAError("invalid path: " + rel)
        parts = rel.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise OTAError("invalid path: " + rel)
        return "/".join(parts)

    def _stage_path(self, rel):
        rel = self._normalize_path(rel)
        return self.stage + "/" + rel

    def _backup_path(self, rel):
        rel = self._normalize_path(rel)
        return self.backup + "/" + rel

    def _try_delta_update(self, rel, entry, ref):
        """Try to use delta update if available and beneficial."""
        # Check if delta updates should be used based on transport
        if not self._should_prefer_delta():
            return False

        # Check if old file exists for delta
        old_file = rel  # File in root directory
        if not _exists(old_file):
            self._debug("Delta: No old file for", rel)
            return False

        # Try to fetch delta from release assets or .deltas directory
        # Format: path/to/file.py.delta.{old_sha}.{new_sha}
        try:
            # For now, look for delta in release assets
            # Server-side would need to generate these
            delta_url = "https://raw.githubusercontent.com/%s/%s/%s/.deltas/%s.delta" % (
                self.cfg["owner"], self.cfg["repo"], ref, rel.replace("/", "_")
            )

            self._debug("Trying delta update for", rel)
            r = self._get(delta_url, raw=True)

            # Download delta to temp file
            delta_path = self._stage_path(rel) + ".delta"
            with open(delta_path, "wb") as f:
                for chunk in http_reader(r)(self.chunk):
                    f.write(chunk)
                    self._feed_watchdog()
            r.close()

            # Apply delta
            try:
                from delta import apply_delta
            except ImportError:
                self._debug("Delta module not available")
                return False

            output_path = self._stage_path(rel) + ".tmp"
            result_hash = apply_delta(
                old_file,
                open(delta_path, "rb").read(),
                output_path,
                expected_hash=entry.get("sha256"),
                chunk_size=self.chunk
            )

            # Verify using git blob hash
            def file_reader(chunk_size):
                with open(output_path, "rb") as f:
                    while True:
                        data = f.read(chunk_size)
                        if not data:
                            break
                        yield data

            size = int(entry.get("size", 0))
            git_hash = git_blob_sha1_stream(size, file_reader, self.chunk)
            if git_hash != entry["sha"]:
                raise OTAError("Delta resulted in incorrect hash")

            # Success! Move to final location
            final_ = self._stage_path(rel)
            try:
                os.remove(final_)
            except OSError:
                pass
            os.rename(output_path, final_)

            # Cleanup
            try:
                os.remove(delta_path)
            except OSError:
                pass

            self._debug("Delta update successful for", rel)
            return True

        except Exception as e:
            self._debug("Delta update failed:", str(e))
            # Clean up any temp files
            for suffix in (".delta", ".tmp"):
                try:
                    os.remove(self._stage_path(rel) + suffix)
                except OSError:
                    pass
            return False

    def stream_and_verify_git(self, entry, ref):
        rel = entry["path"]
        size = int(entry.get("size", 0))
        if size == 0 or entry.get("type") != "blob":
            return

        # Check if file already exists with correct hash to prevent unnecessary flash writes
        final_ = self._stage_path(rel)
        if _exists(final_):
            try:
                # Use git blob hash to verify existing file
                def file_reader(chunk_size):
                    with open(final_, "rb") as f:
                        while True:
                            data = f.read(chunk_size)
                            if not data:
                                break
                            yield data
                existing_hash = git_blob_sha1_stream(size, file_reader, self.chunk)
                if existing_hash == entry["sha"]:
                    self._debug("File unchanged, skipping download:", rel)
                    return
            except Exception:
                # If verification fails, proceed with download
                pass

        # Try delta update first if enabled
        if self._try_delta_update(rel, entry, ref):
            return

        url = "https://raw.githubusercontent.com/%s/%s/%s/%s" % (
            self.cfg["owner"], self.cfg["repo"], ref, rel
        )
        self._debug("Downloading:", rel)
        # Turn LED off during download (will blink via watchdog feeds)
        self._led_set(0)
        r = self._get(url, raw=True)
        tmp = None
        try:
            tmp = self._stage_path(rel) + ".tmp"
            d = tmp.rpartition("/")[0]
            if d:
                ensure_dirs(d)
            # Preemptive GC before large operation
            import gc
            gc.collect()

            f = open(tmp, "wb")
            try:
                chunk_count = 0
                def reader(n):
                    nonlocal chunk_count
                    for chunk in http_reader(r)(n):
                        f.write(chunk)
                        self._feed_watchdog()
                        chunk_count += 1
                        # Collect garbage every 8 chunks instead of 64 to prevent memory fragmentation
                        if chunk_count % 8 == 0:
                            gc.collect()
                        # Brief LED pulse every 10 chunks during download
                        if chunk_count % 10 == 0:
                            self._led_set(1)
                        elif chunk_count % 10 == 1:
                            self._led_set(0)
                        yield chunk
                digest = git_blob_sha1_stream(size, reader, self.chunk)
                f.flush()
                if hasattr(os, "fsync"):
                    os.fsync(f.fileno())
            finally:
                f.close()
            if digest != entry["sha"]:
                raise OTAError("hash mismatch for " + rel)
            final_ = self._stage_path(rel)
            d = final_.rpartition("/")[0]
            if d:
                ensure_dirs(d)
            try:
                os.remove(final_)
            except OSError:
                pass
            os.rename(tmp, final_)
            tmp = None  # Renamed successfully, don't clean up
            if hasattr(os, "sync"):
                try:
                    os.sync()
                except Exception:
                    pass
            # Quick blink to signal file completed
            self._led_blink([(50, 50)])
            self._debug("Hash OK for", rel)
        finally:
            try:
                r.close()
            except Exception:
                pass
            # Clean up temp file if it still exists
            if tmp and _exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    def _download_asset(self, url, dest, expected_sha=None, expected_crc=None, expected_size=None):
        # skip identical write when a strong hash is available
        if expected_sha and _exists(dest):
            try:
                if sha256_file(dest, self.chunk) == expected_sha:
                    return
            except Exception:
                pass

        # Preemptive GC before large operation
        import gc
        gc.collect()

        r = self._get(url, raw=True)
        tmp = dest + ".tmp"
        try:
            h = hashlib.sha256()
            crc = 0
            total = 0
            bufsize = self.chunk
            buf = bytearray(bufsize)
            mv = memoryview(buf)
            src = getattr(r, "raw", None) or r
            readinto = getattr(src, "readinto", None)
            with open(tmp, "wb") as f:
                n_chunks = 0
                if readinto:
                    while True:
                        n = readinto(buf)
                        if not n:
                            break
                        total += n
                        h.update(mv[:n])
                        crc = _crc32_update(crc, mv[:n])
                        f.write(mv[:n])
                        n_chunks += 1
                        # Collect every 8 chunks instead of 64 to prevent memory fragmentation
                        if n_chunks % 8 == 0:
                            gc.collect()
                        self._feed_watchdog()
                else:
                    for block in http_reader(r)(bufsize):
                        total += len(block)
                        h.update(block)
                        crc = _crc32_update(crc, block)
                        f.write(block)
                        n_chunks += 1
                        # Collect every 8 chunks instead of 64 to prevent memory fragmentation
                        if n_chunks % 8 == 0:
                            gc.collect()
                        self._feed_watchdog()
                f.flush()
                if hasattr(os, "fsync"):
                    os.fsync(f.fileno())
            if expected_size is not None and total != expected_size:
                raise OTAError("size mismatch for {}".format(dest))
            sha = _hexdigest(h)
            crc &= 0xFFFFFFFF
            if expected_sha and sha != expected_sha:
                raise OTAError("sha256 mismatch for {}".format(dest))
            if not expected_sha and expected_crc is not None and crc != expected_crc:
                raise OTAError("crc32 mismatch for {}".format(dest))
            os.rename(tmp, dest)
            tmp = None  # Renamed successfully, don't clean up
            if hasattr(os, "sync"):
                try:
                    os.sync()
                except Exception:
                    pass
        finally:
            try:
                r.close()
            except Exception:
                pass
            # Clean up temp file if it still exists
            if tmp and _exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    # --------------------------------------------------------
    # Swap with rollback

    def stage_and_swap(self, applied_ref, applied_commit, deletes=None, safe_tail=None):
        applied = []
        self._debug("Applying update:", applied_ref)
        # Solid LED during file swapping
        self._led_set(1)
        try:
            # move staged files into place
            for root, dirs, files in _walk(self.stage):
                for name in files:
                    stage_path = (root + "/" + name)
                    rel = stage_path[len(self.stage) + 1 :]
                    if not self._is_permitted(rel):
                        continue
                    target = rel
                    backup = self._backup_path(rel)
                    ensure_dirs(backup.rpartition("/")[0])
                    ensure_dirs(target.rpartition("/")[0])
                    if _exists(target):
                        os.rename(target, backup)
                        # CRITICAL: Sync immediately after backup to ensure it's persisted
                        if hasattr(os, "sync"):
                            try:
                                os.sync()
                            except Exception:
                                pass
                        # Track backup immediately to ensure rollback works if next rename fails
                        applied.append((target, backup))
                    else:
                        # No backup created, track None
                        applied.append((target, None))
                    os.rename(stage_path, target)
                    # Sync after applying new file
                    if hasattr(os, "sync"):
                        try:
                            os.sync()
                        except Exception:
                            pass
                    self._feed_watchdog()
            # deletions from manifest
            if deletes:
                for rel in deletes:
                    if not self._is_permitted(rel):
                        continue
                    if _exists(rel):
                        bpath = self._backup_path(rel)
                        ensure_dirs(bpath.rpartition("/")[0])
                        os.rename(rel, bpath)
                        # Sync after backup
                        if hasattr(os, "sync"):
                            try:
                                os.sync()
                            except Exception:
                                pass
                        applied.append((None, bpath))
            # optional conservative deletion for developer channel
            patterns = self.cfg.get("delete_patterns", [])
            if patterns:
                staged_now = set()
                for root, dirs, files in _walk(self.stage):
                    for n in files:
                        rel = (root + "/" + n)[len(self.stage) + 1 :]
                        if not self._is_permitted(rel):
                            continue
                        staged_now.add(rel)
                for root, dirs, files in _walk(""):
                    if root.startswith(self.stage) or root.startswith(self.backup):
                        continue
                    for n in files:
                        rel = (root + "/" + n) if root else n
                        if rel == VERSION_FILE:
                            continue
                        if not self._is_permitted(rel):
                            continue
                        if any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in patterns):
                            if rel not in staged_now and _exists(rel):
                                bpath = self._backup_path(rel)
                                ensure_dirs(bpath.rpartition("/")[0])
                                try:
                                    os.rename(rel, bpath)
                                    # Sync after backup
                                    if hasattr(os, "sync"):
                                        try:
                                            os.sync()
                                        except Exception:
                                            pass
                                    applied.append((None, bpath))
                                except Exception:
                                    pass
            self._write_state(applied_ref, applied_commit)
            if hasattr(os, "sync"):
                try:
                    os.sync()
                except Exception:
                    pass
        except Exception:
            self._debug("Rollback triggered")
            rollback_errors = []
            for target, backup in reversed(applied):
                try:
                    if backup and _exists(backup):
                        if target and _exists(target):
                            os.remove(target)
                        os.rename(backup, target or backup)
                except Exception as e:
                    # Track rollback failures for debugging
                    error_msg = "Failed to rollback {}: {}".format(target or backup, str(e))
                    rollback_errors.append(error_msg)
                    self._debug(error_msg)
            if rollback_errors:
                self._write_error_state(rollback_errors)
            raise
        finally:
            _rmtree(self.stage)
            _rmtree(self.backup)
            ensure_dirs(self.stage)
            ensure_dirs(self.backup)

    def _write_state(self, ref: str, commit: str):
        # Check if state is already current to prevent unnecessary flash writes
        current = self._read_state()
        if current and current.get("ref") == ref and current.get("commit") == commit:
            self._debug("State unchanged, skipping write to preserve flash")
            return

        tmp = VERSION_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"ref": ref, "commit": commit}, f)
            f.flush()
            if hasattr(os, "fsync"):
                os.fsync(f.fileno())
        os.rename(tmp, VERSION_FILE)

    def _write_error_state(self, errors: list):
        """Persist error information for headless debugging."""
        try:
            tmp = ERROR_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"errors": errors}, f)
                f.flush()
                if hasattr(os, "fsync"):
                    os.fsync(f.fileno())
            os.rename(tmp, ERROR_FILE)
        except Exception:
            # Best effort - don't fail if we can't write errors
            pass

    def _read_state(self):
        try:
            with open(VERSION_FILE) as f:
                return json.load(f)
        except Exception:
            return None

    # --------------------------------------------------------
    # Reset handling

    def _perform_reset(self):
        mode = self.cfg.get("reset_mode", "hard")
        if mode == "none":
            self._debug("Not resetting device")
            return
        if mode == "soft" and hasattr(machine, "soft_reset"):
            self._debug("Performing soft reset...")
            machine.soft_reset()
        else:
            self._debug("Performing hard reset...")
            machine.reset()

    # --------------------------------------------------------
    # Signed manifest path for stable release

    def _constant_time_compare(self, a: str, b: str) -> bool:
        """Constant-time string comparison to prevent timing attacks."""
        if len(a) != len(b):
            return False
        result = 0
        for x, y in zip(a.encode(), b.encode()):
            result |= x ^ y
        return result == 0

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
            # Fallback to constant-time comparison
            ok = self._constant_time_compare(expected, sig)
        if not ok:
            raise OTAError("manifest signature mismatch")

    def _stable_with_manifest(self, rel_json, tag, commit):
        asset = None
        for a in rel_json.get("assets", []):
            if a.get("name") == "manifest.json":
                asset = a
                break
        if not asset:
            return None

        self._feed_watchdog()  # Before network operation

        url = asset.get("browser_download_url") or asset["url"]
        r = self._get(url, raw=True)
        try:
            manifest = r.json()
        finally:
            try:
                r.close()
            except Exception:
                pass

        self._feed_watchdog()  # After download

        self._verify_manifest_signature(manifest)
        self._feed_watchdog()  # After verification

        current = self._read_state()
        version = manifest.get("version", tag)
        if self.cfg.get("channel", "stable") == "stable":
            self._debug("Release version:", version)
        if (
            not self.cfg.get("force")
            and current
            and current.get("ref") == version
            and current.get("commit") == commit
        ):
            return {"updated": False}

        self._feed_watchdog()  # Before file loop

        for fi in manifest.get("files", []):
            # Validate and normalize path - raise error for security
            rel = self._normalize_path(fi["path"])

            if not self._is_permitted(rel):
                self._debug("Skip not permitted:", rel)
                continue

            # Additional security: check for hidden directory attempts
            if any(part.startswith('.') and part not in ('.ota_stage', '.ota_backup')
                   for part in rel.split('/')):
                self._debug("Suspicious path with hidden directory:", rel)
                raise OTAError("Security violation: hidden directory in path")

            self._feed_watchdog()  # Between file preparations
            raw_url = "https://raw.githubusercontent.com/%s/%s/%s/%s" % (
                self.cfg["owner"], self.cfg["repo"], tag, rel
            )
            dest = self._stage_path(rel)

            # Validate destination path didn't escape staging directory
            if not dest.startswith(self.stage + "/"):
                self._debug("Path escapes staging directory:", rel)
                raise OTAError("Security violation: path escapes staging")

            ensure_dirs(dest.rpartition("/")[0])
            self._debug("Downloading:", rel)
            self._download_asset(
                raw_url,
                dest,
                expected_sha=fi.get("sha256"),
                expected_crc=fi.get("crc32"),
                expected_size=fi.get("size"),
            )
            if fi.get("sha256"):
                if sha256_file(dest, self.chunk) != fi["sha256"]:
                    raise OTAError("sha256 mismatch after write for " + rel)
            elif fi.get("crc32") is not None:
                if crc32_file(dest, self.chunk) != int(fi["crc32"]):
                    raise OTAError("crc32 mismatch after write for " + rel)
            self._debug("Hash OK for", rel)
        deletes = []
        for d in manifest.get("deletes", []):
            try:
                d = self._normalize_path(d)
            except OTAError as e:
                self._debug("Invalid delete path in manifest:", d, str(e))
                continue
            if self._is_permitted(d):
                deletes.append(d)
            else:
                self._debug("Skip delete not permitted:", d)

        self._feed_watchdog()  # Before swap

        self.stage_and_swap(version, commit, deletes=deletes)
        hook = manifest.get("post_update")
        if hook:
            self._run_hook(hook)
        return {"updated": True}

    # --------------------------------------------------------
    # Public entry point

    def update_if_available(self):
        self.connect()
        if not self._check_basic_resources():
            self._info("Insufficient system resources")
            return False
        self._debug_resources()
        target = self.resolve_target()
        self._debug("Resolving target:", target["mode"], self._format_version(target))
        state = self._read_state()
        self._debug("Installed version:", self._format_version(state))
        self._debug("Repo version:", self._format_version(target))
        if target["mode"] == "tag":
            res = self._stable_with_manifest(target["release_json"], target["ref"], target["commit"])
            if res is not None:
                if res.get("updated"):
                    self._debug("Update required")
                    self._perform_reset()
                    return True
                self._debug("No update required")
                self._info("No update required")
                return False
        if not self.cfg.get("force") and state and state.get("commit") == target["commit"]:
            self._debug("No update required")
            self._info("No update required")
            return False
        self._debug("Update required")
        tree = self.fetch_tree(target["commit"])
        candidates = []
        required = 0
        for entry in self.iter_candidates(tree):
            candidates.append(entry)
            sz = int(entry.get("size", 0))
            required += sz
            # early stop if storage already known to be insufficient
            if not self._check_storage(required * 2):
                break

        # Pre-download validation
        if not self._validate_update_plan(candidates):
            self._info("Update validation failed")
            return False

        if not self._check_storage(required * 2):
            print("Insufficient storage for update")
            return False

        # Show cost estimate if using metered connection
        cost = self._estimate_update_cost(required)
        if cost > 0:
            self._info("Estimated update cost: ${:.2f}".format(cost))

        # Check if delta updates are preferred for this connection
        if self._should_prefer_delta():
            self._debug("Delta updates preferred for this connection type")

        ref_for_download = target["ref"] if target["mode"] == "tag" else target["commit"]
        for entry in candidates:
            self.stream_and_verify_git(entry, ref_for_download)
        self.stage_and_swap(target["ref"], target["commit"])
        # Signal success with three quick blinks
        self._led_blink([(100, 100), (100, 100), (100, 0)])
        self._perform_reset()
        return True

    # --------------------------------------------------------
    # Hook

    def _run_hook(self, path_):
        try:
            mod = path_.replace("/", ".")
            if mod.endswith(".py"):
                mod = mod[:-3]
            # ensure fresh import if hook was updated
            m = sys.modules.pop(mod, None)
            if m is not None:
                del m
            __import__(mod)
        except Exception as exc:
            print("post update hook failed:", exc)

