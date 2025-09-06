"""Example entry point for the ``OtaClient``."""

import json
from ota_client import OtaClient


def load_config():
    with open("ota_config.json") as f:
        cfg = json.load(f)
    placeholders = {"YOUR_GITHUB_USERNAME", "YOUR_REPO_NAME"}
    owner = str(cfg.get("owner", "")).strip().upper()
    repo = str(cfg.get("repo", "")).strip().upper()
    if not owner or owner in placeholders or not repo or repo in placeholders:
        raise ValueError(
            "ota_config.json must define non-placeholder 'owner' and 'repo' values"
        )
    return cfg


def main():
    cfg = load_config()
    ota = OtaClient(cfg)
    try:
        ota.connect()
        ota.update_if_available()
    except Exception as exc:
        print("OTA update failed:", exc)


if __name__ == "__main__":
    main()
