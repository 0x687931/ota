# MicroPython OTA Updater

Robust over‑the‑air (OTA) update system for MicroPython devices.  The updater
can pull application bundles either from the latest GitHub release
(``stable`` channel) or from the tip of a development branch (``developer``
channel).  Every file is streamed through a Git style SHA1 verifier and
staged before an atomic swap with rollback support.

## Features

* Dual update channels – latest release or branch tip
* Works with public and private repositories (token optional)
* Manifest or manifestless operation using the Git tree
* Git blob SHA1 verification (optional SHA256 sidecar)
* Streamed downloads to a staging directory and atomic swap
* Rollback on failure and version tracking
* Minimal memory usage and concise logging

## Usage

1. Copy `ota_client.py` and `main.py` to the device.
2. Provide configuration in `ota_config.json` (an example is included).
   Set ``channel`` to ``stable`` to pull the latest GitHub release or to
   ``developer`` to use the tip of ``branch``.
3. For manifest based updates build a release that contains an asset named
   `manifest.json`.  For manifestless mode the client derives the file list
   directly from the Git tree at the chosen ref.  Use `manifest_gen.py` on
   the development machine to create the manifest if desired:

   ```bash
   python manifest_gen.py --version v1.0.0 boot.py main.py lib/util.py
   ```

4. Upload the manifest and files as release assets.  The updater can then
   fetch the latest release or a specific tag when `CONFIG['tag']` is set.

   If you prefer to avoid attaching a manifest, omit `manifest.json` from
   the release and the updater will derive file paths from the Git tree at
   the tag.  Each file is verified against its Git blob SHA before being
   staged.  Set `CONFIG['paths']` to a list of directories or files to
   restrict which parts of the repository are updated.

### Minimal test update on a Pico W

1. Fork this repository on GitHub so the device can access `README.md`.
2. Create `ota_config.json` on the Pico with values similar to:

   ```json
   {
     "owner": "YOUR_GITHUB_USERNAME",
     "repo": "ota",
     "ssid": "YOUR_WIFI_SSID",
     "password": "YOUR_WIFI_PASSWORD",
     "channel": "developer",
     "branch": "main",
     "token": "",
     "allow": ["README.md"],
     "ignore": [],
     "chunk": 512,
     "connect_timeout_sec": 20,
     "http_timeout_sec": 20,
     "retries": 3,
     "backoff_sec": 3,
     "debug": false
   }
   ```

   Set `debug` to `true` to enable verbose logging for troubleshooting.

   The configuration fields are:

   - `owner` (string) – GitHub username.
   - `repo` (string) – repository name.
   - `ssid` (string) – Wi‑Fi network name.
   - `password` (string) – Wi‑Fi password.
   - `channel` (string) – `stable` for releases or `developer` for branch tip.
   - `branch` (string) – development branch when using the `developer` channel.
   - `token` (string) – GitHub API token; use an empty string (`""`) for public repositories.
   - `allow` (list of strings) – whitelist of paths to update.
   - `ignore` (list of strings) – paths to skip during updates.
  - `chunk` (integer) – download buffer size in bytes.
  - `connect_timeout_sec` / `http_timeout_sec` (integer) – network timeout values.
    On MicroPython the two fields collapse into one effective timeout equal to
    the larger of the provided values.
  - `retries` (integer) – number of retry attempts.
  - `backoff_sec` (integer) – delay between retries in seconds.
  - `debug` (boolean) – set to `true` for verbose logging.

   Booleans must use lowercase `true` or `false` without quotes.

3. Copy `ota_client.py`, `main.py` and the config file to the root of the Pico.
4. Run the updater from the REPL:

   ```python
   import main
   main.main()
   ```

   The client downloads `README.md`, verifies its SHA1 and reboots into the
   updated filesystem.  Editing `README.md` on GitHub and rerunning will fetch
   the new revision.

## Compatibility

The modules expose a ``MICROPYTHON`` flag based on ``sys.implementation.name``
to detect when running under MicroPython and fall back to lightweight stubs on
CPython.  The codebase has been verified on MicroPython v1.26.0 (2025-08-09)
running on a Raspberry Pi Pico W with an RP2040.

## Testing

The repository includes unit tests that exercise hash verification,
resolve logic and file staging with rollback.  Run the tests on a
development machine with Python 3:

```bash
pytest
```

## Security Notes

* The GitHub token for private repositories should be stored in a small
  configuration file or passed at runtime.  On MicroPython devices
  secrets are stored in plain text – protect physical access to the device.
* TLS certificate validation may be limited on some boards.  When using
  `urequests`, ensure the firmware supports HTTPS or provide a CA bundle
  if necessary.

## Integration

After a successful update the device writes the new version to
`version.json` and issues `machine.reset()` to boot into the new code.
The manifest may include optional `post_update` and `rollback` hook
scripts for custom actions.

## License

MIT License
