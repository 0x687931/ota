"""Example entry point for the ``OtaClient``."""

import json
from ota_client import OtaClient


def load_config():
    with open("ota_config.json") as f:
        return json.load(f)


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
