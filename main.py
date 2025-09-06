"""Example entry point for the ``OTA`` updater."""

import json
import os

from ota import OTA

try:  # CPython 3.11
    import tomllib  # type: ignore
except Exception:  # MicroPython or earlier CPython
    tomllib = None


def _splitext(p: str):
    i = p.rfind(".")
    return (p[:i], p[i:]) if i != -1 else (p, "")


def _basename(p: str):
    j = p.rfind("/")
    return p[j + 1:] if j != -1 else p


def load_config(config_path: str = "ota_config.json"):
    """Load configuration from JSON, YAML or TOML based on extension."""
    try:
        with open(config_path, "r") as f:
            text = f.read()
    except Exception as exc:
        raise RuntimeError("Config file not found: {}".format(config_path)) from exc
    _, ext = _splitext(config_path)
    ext = ext.lower()
    if ext in (".yaml", ".yml"):
        try:
            import yaml
        except Exception as exc:  # pragma: no cover - missing dependency
            raise RuntimeError("PyYAML is required for YAML config files") from exc
        cfg = yaml.safe_load(text) or {}
    elif ext == ".toml":
        if tomllib is None:
            raise RuntimeError("TOML config requires CPython 3.11 or tomllib. Use JSON on device.")
        cfg = tomllib.loads(text)
    else:
        cfg = json.loads(text)
    placeholders = {"YOUR_GITHUB_USERNAME", "YOUR_REPO_NAME"}
    owner = str(cfg.get("owner", "")).strip().upper()
    repo = str(cfg.get("repo", "")).strip().upper()
    if not owner or owner in placeholders or not repo or repo in placeholders:
        raise ValueError(
            "{} must define non placeholder 'owner' and 'repo' values".format(_basename(config_path))
        )
    return cfg


def main():
    cfg = load_config()
    ota = OTA(cfg)
    try:
        ota.update_if_available()
    except Exception as exc:
        print("OTA update failed:", exc)


if __name__ == "__main__":
    main()
