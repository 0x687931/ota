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
