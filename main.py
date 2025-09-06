"""Example entry point for the ``OTA`` updater."""

import json
from pathlib import Path

from ota import OTA

try:  # Python 3.11+
    import tomllib  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - earlier versions
    tomllib = None


def load_config(config_path: str = "ota_config.json"):
    """Load configuration from JSON, YAML or TOML based on extension."""
    path = Path(config_path)
    text = path.read_text()
    ext = path.suffix.lower()
    if ext in (".yaml", ".yml"):
        try:
            import yaml
        except Exception as exc:  # pragma: no cover - missing dependency
            raise RuntimeError("PyYAML is required for YAML config files") from exc
        cfg = yaml.safe_load(text) or {}
    elif ext == ".toml":
        if tomllib is None:
            raise RuntimeError("TOML config requires Python 3.11 or the tomllib module")
        cfg = tomllib.loads(text)
    else:
        cfg = json.loads(text)
    placeholders = {"YOUR_GITHUB_USERNAME", "YOUR_REPO_NAME"}
    owner = str(cfg.get("owner", "")).strip().upper()
    repo = str(cfg.get("repo", "")).strip().upper()
    if not owner or owner in placeholders or not repo or repo in placeholders:
        raise ValueError(
            f"{path.name} must define non-placeholder 'owner' and 'repo' values"
        )
    return cfg


def main():
    cfg = load_config()
    ota = OTA(cfg)
    try:
        ota.connect()
        ota.update_if_available()
    except Exception as exc:
        print("OTA update failed:", exc)


if __name__ == "__main__":
    main()
