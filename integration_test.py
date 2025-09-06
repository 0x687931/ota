"""Toggle update channel and perform a dry run against the configured repo."""

import json
from ota import OTA


def run(channel):
    with open("ota_config.json") as f:
        cfg = json.load(f)
    cfg["channel"] = channel
    client = OTA(cfg)
    # This is a dry run – in real usage ``update_if_available`` would
    # download and apply updates.  Here we simply resolve the target to
    # demonstrate channel selection.
    target = client.resolve_target()
    print("Channel %s -> %s" % (channel, target["commit"]))


def main():
    for ch in ("stable", "developer"):
        try:
            run(ch)
        except Exception as exc:
            print("Channel %s failed: %s" % (ch, exc))


if __name__ == "__main__":
    main()
