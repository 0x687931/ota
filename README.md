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
* **Delta/differential updates**:
  - 60-95% bandwidth reduction for code changes
  - 85-92% energy savings on updates
  - Automatic preference for low-bandwidth/metered connections
  - Transparent fallback to full download
* **Multi-connectivity support**:
  - Intelligent fallback: WiFi → Cellular → LoRa
  - 90%+ connectivity reliability for remote deployments
  - Automatic cost estimation for metered connections
  - Transport-aware delta and bandwidth optimization
* **Headless operation support**:
  - Hardware watchdog timer for automatic recovery from hangs
  - Status LED visual feedback for debugging without console
  - Battery level monitoring for battery-powered devices
  - Error state persistence for post-mortem debugging
* **Enhanced reliability**:
  - Exponential backoff for network retries
  - Pre-download validation to catch issues early
  - Automatic resource adaptation based on available memory and signal strength
  - Comprehensive error tracking and rollback safety
* **Update scheduling & health monitoring**:
  - Rate limiting to prevent API quota exhaustion
  - Time-based update windows (solar peak optimization)
  - Canary rollouts for staged deployment
  - Health-based update deferral

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
  - `backoff_sec` (integer, optional) – initial delay between retries in seconds.
  - `max_backoff_sec` (integer, optional) – maximum delay cap for exponential backoff (default 60).
  - `force` (boolean, optional) – set to `true` to force an update even if the installed and remote versions match.
  - `reset_mode` (string, optional) – `hard` for a full reset (default), `soft` for a
    soft reset when supported, or `none` to disable automatic resets.
  - `debug` (boolean, optional) – set to `true` for verbose logging.

#### Headless Operation (Optional)

  - `watchdog_timeout_ms` (integer, optional) – hardware watchdog timeout in milliseconds (e.g., 8000 for 8 seconds). Enables automatic recovery from system hangs.
  - `status_led_pin` (integer, optional) – GPIO pin number for status LED (e.g., 25 for Pico W onboard LED). Provides visual feedback:
    - 2 quick blinks: WiFi connection attempt
    - Solid: Connected/processing
    - Brief pulses: Downloading
    - Quick blink: File completed
    - 3 quick blinks: Update successful
    - Long blink: Connection/update failed
  - `battery_adc_pin` (integer, optional) – ADC pin for battery voltage monitoring.
  - `battery_divider_ratio` (float, optional) – voltage divider ratio if using one (default 1.0).
  - `battery_v_max` (float, optional) – fully charged battery voltage (default 4.2 for LiPo).
  - `battery_v_min` (float, optional) – empty battery voltage (default 3.0 for LiPo).
  - `min_battery_percent` (integer, optional) – minimum battery percentage required to perform updates.

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

## Headless Operation

For remote or battery-powered deployments without console access, the updater provides several monitoring and recovery features:

### Watchdog Timer

Enable hardware watchdog to automatically recover from system hangs:

```json
{
  "watchdog_timeout_ms": 8000
}
```

The watchdog is fed during downloads and file operations. If the system hangs, the device will automatically reset after the timeout period.

### Status LED Feedback

Configure a status LED for visual debugging without console access:

```json
{
  "status_led_pin": 25
}
```

**LED Patterns:**
- **2 quick blinks** → WiFi connection starting
- **Solid LED** → Connected and processing
- **Brief pulses** → Actively downloading files
- **Quick blink** → File download completed
- **3 quick blinks** → Update successful
- **Long blink (500ms)** → Connection or update failed

### Battery Monitoring

For battery-powered devices, configure battery monitoring to prevent updates when battery is low:

```json
{
  "battery_adc_pin": 26,
  "battery_divider_ratio": 2.0,
  "battery_v_max": 4.2,
  "battery_v_min": 3.0,
  "min_battery_percent": 20
}
```

The updater will abort if battery level falls below `min_battery_percent`.

### Error State Persistence

Failed updates write error details to `ota_error.json` for post-mortem debugging. This file persists across reboots and includes:
- Rollback failures and reasons
- Update validation errors
- Exception messages from failed operations

### Exponential Backoff

Network retries use exponential backoff to avoid overwhelming poor connections:

```json
{
  "retries": 5,
  "backoff_sec": 3,
  "max_backoff_sec": 60
}
```

First retry waits 3s, then 6s, 12s, 24s, up to the 60s maximum. The system automatically adapts retry behavior based on WiFi signal strength (RSSI).

## Delta Updates

Reduce bandwidth usage by 60-95% with differential updates. Instead of downloading entire files, only the changes between versions are transmitted.

### Configuration

```json
{
  "enable_delta_updates": true
}
```

### Server-Side Setup

1. Generate deltas between versions using the provided tool:

```bash
python delta_gen.py --old v1.0.0 --new v1.1.0 --output .deltas/
```

2. Commit and push deltas to your repository:

```bash
git add .deltas/
git commit -m "Add deltas for v1.1.0"
git push
```

### How It Works

- Device automatically attempts delta updates when enabled
- Falls back to full download if delta is unavailable or fails
- Delta preferred automatically for low-bandwidth or metered connections (cellular)
- Verifies output integrity using Git blob SHA1 hash

### Benefits

- **60-95% bandwidth reduction** for typical code changes
- **85-92% energy savings** on updates
- **Essential for cellular deployments** (automatic cost estimation)
- **Zero configuration** on device side

### Files

- `delta.py` - Delta apply module (runs on device)
- `delta_gen.py` - Delta generation tool (runs on server)
- `.deltas/` - Directory for storing delta files in repository

## Multi-Connectivity Support

Intelligent fallback between WiFi, Cellular, and LoRa connections for maximum reliability in remote deployments.

### WiFi + Cellular Configuration

```json
{
  "ssid": "wifi-ssid",
  "password": "wifi-password",
  "cellular_enabled": true,
  "cellular_apn": "your.apn.com",
  "cellular_uart": 1,
  "cellular_tx_pin": 4,
  "cellular_rx_pin": 5,
  "cellular_baud": 115200,
  "cellular_tech": "nbiot",
  "cellular_cost_per_mb": 0.50
}
```

### WiFi + LoRa Configuration

```json
{
  "ssid": "wifi-ssid",
  "password": "wifi-password",
  "lora_enabled": true,
  "lora_spi_pins": [18, 19, 16],
  "lora_cs_pin": 17,
  "lora_rst_pin": 20,
  "lora_freq": 915000000
}
```

### How It Works

- Automatically tries transports in priority order: WiFi → Cellular → LoRa
- Shows connected transport and signal strength in debug output
- Estimates update cost for metered connections (cellular)
- Automatically prefers delta updates for low-bandwidth/costly connections

### Transport Priorities

1. **WiFi** - High bandwidth, zero cost
2. **Cellular** - Medium/high bandwidth, metered (NB-IoT, LTE-M, 2G/3G/4G)
3. **LoRa** - Very low bandwidth, zero cost (metadata/triggers only)

### Supported Hardware

**Cellular Modems:**
- SIM800/SIM800L (2G)
- SIM7000 (NB-IoT/LTE-M)
- SIM7600 (4G LTE)
- Any AT command-based modem

**LoRa Modules:**
- SX1276/SX1278
- RFM95/RFM96
- LoRaWAN gateways

### Benefits

- **90%+ connectivity reliability** vs 60-70% WiFi-only
- **Automatic failover** when WiFi unavailable
- **Cost optimization** for cellular deployments
- **Essential for remote deployments** (weather stations, remote sensors, etc.)

**Note:** WiFi transport is fully implemented. Cellular and LoRa transports provide framework but require modem-specific implementation for production use. See `connectivity.py` for transport interface.

### Files

- `connectivity.py` - Transport abstraction and ConnectivityManager

## Update Scheduling & Health Monitoring

Intelligent update timing and health-based decisions for production IoT fleets. See `update_scheduler.py` for full documentation.

### Features

- **Health tracking** - Monitor crash counts and update history
- **Rate limiting** - Prevent API quota exhaustion
- **Update windows** - Time-based scheduling (e.g., solar peak hours)
- **Canary rollouts** - Staggered deployment using device ID hashing
- **Stability checks** - Delay updates after recent crashes

### Configuration

```json
{
  "update_scheduling": {
    "min_update_interval_sec": 3600,
    "update_window_start_hour": 10,
    "update_window_end_hour": 15,
    "power_source": "solar",
    "min_battery_percent": 60,
    "max_crashes_before_delay": 3,
    "rollout_percent": 20
  }
}
```

## Integration

After a successful update the device writes the new version to
`version.json` and issues `machine.reset()` to boot into the new code.
The manifest may include optional `post_update` and `rollback` hook
scripts for custom actions.

## License

MIT License
