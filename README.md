# MicroPython OTA Updater

This project was originally forked from [kevinmcaleer/ota](https://github.com/kevinmcaleer/ota) by Kevin McAleer.

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

1. Copy `ota.py` and `main.py` to the device.
2. Provide configuration in `ota_config.json` (default), `ota_config.yaml`
   / `ota_config.yml`, or `ota_config.toml`.  The loader inspects the file
   extension to parse JSON, YAML or TOML.  YAML parsing requires the optional
   [PyYAML](https://pyyaml.org/) dependency; TOML uses Python's built‑in
   `tomllib` (3.11+).  Set ``channel`` to ``stable`` to pull the latest
   GitHub release or to ``developer`` to use the tip of ``branch``.
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
   staged.  Use the `allow` and `ignore` lists in the configuration to
   control which files are downloaded.  Entries may be exact file names
   like `ota.py` or directory prefixes such as `lib/`; `ignore` rules use
   the same matching logic and take priority over `allow`.

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
     "stage_dir": ".ota_stage",
     "backup_dir": ".ota_backup",
     "connect_timeout_sec": 20,
     "http_timeout_sec": 20,
     "retries": 3,
     "backoff_sec": 3,
     "reset_mode": "hard",
     "debug": false
   }
   ```

   Equivalent YAML:

   ```yaml
   owner: YOUR_GITHUB_USERNAME
   repo: ota
   ssid: YOUR_WIFI_SSID
   password: YOUR_WIFI_PASSWORD
   channel: developer
   branch: main
   allow: [README.md]
   ignore: []
   chunk: 512
   connect_timeout_sec: 20
   http_timeout_sec: 20
   retries: 3
   backoff_sec: 3
   reset_mode: hard
   debug: false
   ```

   Equivalent TOML:

   ```toml
   owner = "YOUR_GITHUB_USERNAME"
   repo = "ota"
   ssid = "YOUR_WIFI_SSID"
   password = "YOUR_WIFI_PASSWORD"
   channel = "developer"
   branch = "main"
   allow = ["README.md"]
   ignore = []
   chunk = 512
   connect_timeout_sec = 20
   http_timeout_sec = 20
   retries = 3
   backoff_sec = 3
   force = false
   reset_mode = "hard"
   debug = false
   ```

   Set `debug` to `true` to enable verbose logging for troubleshooting.
   The `reset_mode` field controls how the device restarts after an update:
   `hard` (default) uses `machine.reset()`, `soft` attempts `machine.soft_reset()`,
   and `none` skips resetting.

   The configuration fields are:

  - `owner` (string, required) – GitHub username.
  - `repo` (string, required) – repository name.
  - `ssid` (string, required) – Wi‑Fi network name.
  - `password` (string, required) – Wi‑Fi password.
  - `channel` (string, required) – `stable` for releases or `developer` for branch tip.
  - `branch` (string, optional) – development branch when using the `developer` channel.
  - `token` (string, optional) – GitHub API token; use an empty string (`""`) for public repositories.
  - `allow` (list of strings, required) – whitelist of paths to update.
  - `ignore` (list of strings, optional) – paths to skip during updates.
  - `chunk` (integer, optional) – download buffer size in bytes.
  - `stage_dir` (string, optional) – staging directory used during updates; defaults to `.ota_stage`.
  - `backup_dir` (string, optional) – directory holding backups for rollback; defaults to `.ota_backup`.
  - `connect_timeout_sec` / `http_timeout_sec` (integer, optional) – network timeout values.
    On MicroPython the two fields collapse into one effective timeout equal to
    the larger of the provided values.
  - `retries` (integer, optional) – number of retry attempts.
  - `backoff_sec` (integer, optional) – delay between retries in seconds.
  - `force` (boolean, optional) – set to `true` to force an update even if the installed and remote versions match.
  - `reset_mode` (string, optional) – `hard` for a full reset (default), `soft` for a
    soft reset when supported, or `none` to disable automatic resets.
  - `debug` (boolean, optional) – set to `true` for verbose logging.


### Path filtering

The updater applies `allow` and `ignore` rules to every file considered
for download.  Each rule may be an exact file (e.g. `main.py`) or a
directory prefix (`lib/` or `lib`).  `ignore` entries take precedence
over `allow`.  When `allow` is empty all files are permitted unless
ignored.  Manifest files and delete instructions are subject to the same
checks, and paths containing `..` or starting with `/` are rejected.

Booleans must use lowercase `true` or `false` without quotes.

3. Copy `ota.py`, `main.py` and the config file to the root of the Pico.
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

Run a quick smoke test to verify that the client can resolve the update
target for each channel without applying changes:

```bash
python integration_test.py
```

For comprehensive coverage, run the unit tests on a development
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
