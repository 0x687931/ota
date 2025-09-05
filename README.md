# MicroPython OTA Updater

Robust over‑the‑air (OTA) update system for MicroPython devices.  The updater
pulls application bundles from GitHub releases, verifies integrity with
cryptographic hashes and swaps files atomically with rollback support.

## Features

* Fetch latest or specific GitHub release tag
* Works with public and private repositories (token optional)
* Manifest driven updates describing files, sizes and hashes
* SHA256 verification (CRC32 fallback)
* Streamed downloads to a staging directory and atomic swap
* Rollback on failure and version tracking
* Minimal memory usage and concise logging

## Usage

1. Copy `ota_updater.py` and `main.py` to the device.
2. Edit the `CONFIG` dictionary in `main.py` with Wi‑Fi credentials and
   GitHub repository information.  For private repositories provide a
   personal access token.
3. Build a release on GitHub that contains an asset named `manifest.json`
   describing the files in the release.  Use `manifest_gen.py` on the
   development machine to create the manifest:

   ```bash
   python manifest_gen.py --version v1.0.0 boot.py main.py lib/util.py
   ```

4. Upload the manifest and files as release assets.  The updater can then
   fetch the latest release or a specific tag when `CONFIG['tag']` is set.

## Testing

The repository includes unit tests that exercise hash verification,
file staging and rollback logic.  Run the tests on a development
machine with Python 3:

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
