"""Example entry point for the ``OtaClient``."""

import json
import traceback
from ota_client import OtaClient


def load_config():
    with open("ota_config.json") as f:
        return json.load(f)


def main():
    try:
        cfg = load_config()
        ota = OtaClient(cfg)
        ota.connect()
        ota.update_if_available()
    except Exception as exc:
        cfg_local = locals().get("cfg", {})
        if cfg_local.get("debug"):
            traceback.print_exc()
        print("OTA update failed:", exc)


if __name__ == "__main__":
    main()
