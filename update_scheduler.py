"""
Update Scheduling & Health Monitoring for OTA System

Provides intelligent update timing and health-based decisions for production IoT deployments.
"""

import os
import sys

try:
    import ujson as json
except Exception:
    import json

MICROPYTHON = sys.implementation.name == "micropython"

HEALTH_LOG_FILE = "ota_health.json"
RATE_LIMIT_FILE = "ota_last_check.json"


class UpdateScheduler:
    """Intelligent update timing and health-based decisions."""

    def __init__(self, ota_client):
        self.ota = ota_client
        self.cfg = ota_client.cfg
        self.health_log = self._load_health_log()

    def _load_health_log(self):
        """Load health log from disk."""
        try:
            with open(HEALTH_LOG_FILE) as f:
                return json.load(f)
        except Exception:
            return {"crashes": [], "errors": [], "updates": []}

    def _save_health_log(self):
        """Save health log to disk."""
        try:
            tmp = HEALTH_LOG_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.health_log, f)
                f.flush()
                if hasattr(os, "fsync"):
                    os.fsync(f.fileno())
            os.rename(tmp, HEALTH_LOG_FILE)
        except Exception:
            pass

    def log_health_event(self, event_type, details):
        """Track crashes, memory errors, network failures."""
        try:
            import time
            entry = {
                "timestamp": time.time() if MICROPYTHON else int(time.time()),
                "details": str(details)
            }
            self.health_log.setdefault(event_type, []).append(entry)

            # Keep only last 100 events per type
            max_history = int(self.cfg.get("error_history_limit", 100))
            if len(self.health_log[event_type]) > max_history:
                self.health_log[event_type] = self.health_log[event_type][-max_history:]

            self._save_health_log()
        except Exception:
            pass

    def _recent_crashes(self):
        """Count watchdog resets in last 24 hours."""
        try:
            import time
            now = time.time() if MICROPYTHON else int(time.time())
            recent = [e for e in self.health_log.get("crashes", [])
                      if now - e["timestamp"] < 86400]
            return len(recent)
        except Exception:
            return 0

    def _get_hour(self):
        """Get current hour of day (0-23)."""
        if not MICROPYTHON:
            import time
            return time.localtime().tm_hour
        try:
            import time
            return time.localtime()[3]  # tm_hour
        except Exception:
            return 12  # Default to noon if time unavailable

    def _get_device_id(self):
        """Get unique device identifier."""
        if not MICROPYTHON:
            import socket
            return socket.gethostname()
        try:
            import machine
            import ubinascii
            return ubinascii.hexlify(machine.unique_id()).decode()
        except Exception:
            return "unknown"

    def _is_in_rollout_cohort(self, target_version):
        """Staggered rollout using device ID hash."""
        import hashlib

        device_id = self._get_device_id()
        target_hash = hashlib.sha256(
            "{}:{}".format(target_version, device_id).encode()
        ).hexdigest()

        # Convert to 0-100 percentile
        percentile = int(target_hash[:8], 16) % 100

        # Get rollout percentage from config (default 100% = immediate rollout)
        rollout_percent = int(self.cfg.get("rollout_percent", 100))

        return percentile < rollout_percent

    def _in_update_window(self):
        """Check if current time is within allowed update window."""
        start_hour = self.cfg.get("update_window_start_hour")
        end_hour = self.cfg.get("update_window_end_hour")

        if start_hour is None or end_hour is None:
            return True  # No window configured, allow anytime

        current_hour = self._get_hour()

        if start_hour <= end_hour:
            return start_hour <= current_hour < end_hour
        else:
            # Window crosses midnight
            return current_hour >= start_hour or current_hour < end_hour

    def _check_rate_limit(self):
        """Enforce minimum interval between update checks."""
        min_interval_sec = int(self.cfg.get("min_update_interval_sec", 3600))  # Default 1 hour

        try:
            with open(RATE_LIMIT_FILE) as f:
                state = json.load(f)
                last_check = state.get("last_check", 0)
        except Exception:
            last_check = 0

        import time
        current_time = time.time() if MICROPYTHON else int(time.time())
        elapsed = current_time - last_check

        if elapsed < min_interval_sec:
            remaining = min_interval_sec - elapsed
            return False, remaining

        return True, 0

    def _record_update_check(self):
        """Record the time of this update check."""
        import time
        try:
            tmp = RATE_LIMIT_FILE + ".tmp"
            with open(tmp, "w") as f:
                current_time = time.time() if MICROPYTHON else int(time.time())
                json.dump({"last_check": current_time}, f)
                f.flush()
                if hasattr(os, "fsync"):
                    os.fsync(f.fileno())
            os.rename(tmp, RATE_LIMIT_FILE)
        except Exception:
            pass

    def should_update_now(self, target_version):
        """Multi-factor decision on update timing."""

        # 1. Health check: Recent crash count
        max_crashes = int(self.cfg.get("max_crashes_before_delay", 3))
        if self._recent_crashes() > max_crashes:
            print("[Scheduler] Delaying update: System unstable ({} recent crashes)".format(
                self._recent_crashes()))
            return False

        # 2. Battery level (if configured)
        min_battery = self.cfg.get("min_battery_percent")
        if min_battery is not None:
            battery = self.ota._battery_level()
            if battery is not None and battery < min_battery:
                print("[Scheduler] Delaying update: Low battery ({:.1f}%)".format(battery))
                return False

        # 3. Update window
        if not self._in_update_window():
            print("[Scheduler] Delaying update: Outside update window")
            return False

        # 4. Canary rollout control
        if not self._is_in_rollout_cohort(target_version):
            print("[Scheduler] Delaying update: Not in rollout cohort yet")
            return False

        # 5. Solar charging window (if configured)
        if self.cfg.get("power_source") == "solar":
            hour = self._get_hour()
            if not (10 <= hour <= 15):
                print("[Scheduler] Delaying update: Outside solar peak hours (10-15)")
                return False

        return True

    def record_update_attempt(self, success, version, error=None):
        """Record update attempt for tracking."""
        import time
        entry = {
            "timestamp": time.time() if MICROPYTHON else int(time.time()),
            "version": version,
            "success": success
        }
        if error:
            entry["error"] = str(error)

        self.health_log.setdefault("updates", []).append(entry)

        # Keep only last 50 update attempts
        if len(self.health_log["updates"]) > 50:
            self.health_log["updates"] = self.health_log["updates"][-50:]

        self._save_health_log()
