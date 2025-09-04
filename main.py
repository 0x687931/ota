"""Example entry point demonstrating OTAUpdater usage."""

from ota_updater import OTAUpdater

CONFIG = {
    "ssid": "YOUR_WIFI_SSID",
    "password": "YOUR_WIFI_PASSWORD",
    "repo_owner": "yourname",
    "repo_name": "yourrepo",
    # "tag": "v1.0.0",  # optional specific release tag
    # "token": "ghp_...",  # optional token for private repos
}


def main() -> None:
    ota = OTAUpdater(CONFIG)
    try:
        ota.update()
    except Exception as exc:
        print("OTA update failed:", exc)


if __name__ == "__main__":
    main()
