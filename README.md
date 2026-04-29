# xTool Laser

Home Assistant integration for xTool laser cutters and engravers (S1, D1, D1 Pro, F1, P2, M1, and others).

This integration communicates directly with your xTool device over the local network using the reverse-engineered WebSocket and REST protocols. No cloud connection required.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/thecodingdad/ha-xtool)](https://github.com/thecodingdad/ha-xtool/releases)

## Features

- Full local control — no cloud, no xTool account required
- Monitor device status (idle, processing, paused, finished, error, ...)
- Control light brightness
- Toggle buzzer, move stop, exhaust fan
- Configure air-assist post-run, exhaust post-run
- Flame alarm sensitivity (high / low / off)
- Job control buttons (pause, resume, cancel, home axes)
- Laser position tracking (X/Y/Z)
- Lifetime statistics (working time, session count, standby time, laser module runtime)
- Laser module detection (type + power)
- Attached accessories detection (air pump, fire extinguisher, riser base)
- Camera support for P2 / P2S / F1 Ultra (overview + close-up + flame record)
- Firmware update entity — checks the xTool cloud for new firmware (install off by default, opt-in with confirmation)
- Optional power switch linking (smart plug control)
- Push state updates via WebSocket (S1) — minimal polling delay
- Automatic reconnect on network interruption
- Multi-model support with auto-detected protocol

## Prerequisites

- Home Assistant 2025.1.0 or newer
- xTool device on the same local network, connected via WiFi

## Supported Devices

The integration supports four protocol families that cover all current xTool models:

| Protocol | Models | Communication |
|----------|--------|---------------|
| WebSocket M-code | S1 | WebSocket (port 8081) + HTTP (port 8080) |
| HTTP M-code | D1, D1 Pro, D1 Pro 2.0 | HTTP POST (port 8080) |
| REST API | F1, F1 Ultra, F1 Lite (GS005), M1, M1 Ultra, P1, P2, P2S | HTTP REST (ports 8080 / 8087 / 8329) |
| TLS WebSocket listener | F1 V2 (firmware 40.51+) | wss (port 28900), read-only push |

> **Note:** The integration was developed and tested with an xTool S1. Other models are supported based on reverse engineering of the xTool Android app (XCS) but have not been tested with real hardware. Community feedback is welcome!

## Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=thecodingdad&repository=ha-xtool&category=integration)

Or add manually:
1. Open HACS in your Home Assistant instance
2. Click the three dots in the top right corner and select **Custom repositories**
3. Enter `https://github.com/thecodingdad/ha-xtool` and select **Integration** as the category
4. Click **Add**, then search for "xTool Laser" and download it
5. Restart Home Assistant

### Manual Installation

1. Download the latest release from [GitHub Releases](https://github.com/thecodingdad/ha-xtool/releases)
2. Copy the `custom_components/xtool` folder to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

## Configuration

### Adding a Device

1. Go to **Settings** > **Devices & Services** > **Add Integration**
2. Search for **xTool Laser**
3. The integration scans your network for xTool devices via UDP broadcast
4. Select a discovered device from the list, or choose **Enter IP address manually**
5. Confirm to add the device

### Options

After adding a device, you can configure it under **Settings** > **Devices & Services** > **xTool Laser** > **Configure**:

| Option | Description |
|--------|-------------|
| Power switch | Link an existing switch entity (e.g. a smart plug) that controls the laser's power supply. When the switch is off, the status shows "Off" and entities become unavailable. |
| Enable firmware updates | Off by default — the firmware update entity only reports whether an update is available. Enabling this option arms the install action. |
| AP2 air cleaner | **S1 only.** Opt-in toggle that adds the AP2 air-cleaner sensors (running / connected / speed / filter remaining / dust sensors) and starts polling the air-cleaner push frame. Leave off if you do not own an AP2. |

## Entities

> **Note:** Not all entities are available on every model. The integration automatically detects your device model and connected accessories, and only creates entities for supported features.

### Sensors

| Entity | Description |
|--------|-------------|
| Status | Device status: Off, Idle, Processing, Paused, Finished, Error, etc. Always available — shows "Off" when device is unreachable |
| Laser position X/Y/Z | Current laser head position in mm |
| Fire level | Current flame detection level |
| Air assist level | Current air assist gear (0-4) |
| Task ID | Currently loaded job identifier |
| Task time | Current job elapsed time |
| Working time | Lifetime working hours (total_increasing) |
| Session count | Total number of job starts (total_increasing) |
| Standby time | Lifetime standby hours (total_increasing) |
| Laser module runtime | Current laser module runtime hours (total_increasing) |

### Diagnostic Sensors

| Entity | Description |
|--------|-------------|
| IP address | Device IP address |
| Laser power | Laser module power in watts |
| Laser module | Laser module type (e.g. "Diode", "IR") |
| SD card | SD card inserted / not inserted |
| Workspace size | Device build dimensions (e.g. "498 × 330 × 58 mm"). **S1 only.** |
| Active connections | Number of WebSocket clients currently connected to the laser (HA + XCS app + …). Useful to spot when the XCS app has kicked the integration. **S1 only.** |
| Origin offset X / Y | Last set work-area origin offset. **D-series only.** |
| Last distance | Last IR distance measurement result. **P2 / P2S only.** |
| Last job time / Working mode / Last button event | F1 V2 diagnostic snapshot from push frames. **F1 V2 only.** |
| Purifier speed / filter remaining (pre / medium / carbon / dense carbon / HEPA) / sensor D / sensor S | AP2 air-cleaner telemetry. **S1 with AP2 only.** |

### Light

| Entity | Description |
|--------|-------------|
| Fill light | Dimmable work light (0-100%) |

### Switches

| Entity | Description |
|--------|-------------|
| Buzzer | Enable/disable audio feedback |
| Move stop | Enable/disable emergency movement stop |
| Exhaust fan | Enable/disable smoke extraction fan |
| Power | Controls the linked smart plug (only when configured in options) |
| Tilt stop / Limit stop / Move stop (D-series) | Per-sensor safety toggles. **D-series only.** |
| IR LED close-up / IR LED global | Cover and global IR illumination. **P2 / P2S only.** |
| Digital lock | Cover digital lock control. **P2 / P2S only.** |
| Flame alarm V2 / Beep V2 / Gap check / Machine lock check | Read-only push toggles mirroring device config. **F1 V2 only.** |

### Numbers

| Entity | Range | Unit | Description |
|--------|-------|------|-------------|
| Air-assist post-run | 0-600 | seconds | Time the air assist stays on after a job ends |
| Exhaust post-run | 1-600 | seconds | Time the exhaust fan stays on after a job ends |
| Tilt threshold / Moving threshold | 0-255 | — | D-series sensor sensitivities |
| Camera exposure (overview / close-up) | 0-255 | — | Camera exposure values. **P2 / P2S / F1 / F1 Ultra only.** |
| Air-Assist gear (cut / engrave) | 0-4 | — | Default Air-Assist gear written to user config — applied to next job. **M1 Ultra only; only available when an Air-Assist accessory is attached.** |
| Purifier timeout | 0-3600 | seconds | F1 V2 air-purifier auto-off (read-only push) |

### Select

| Entity | Options | Description |
|--------|---------|-------------|
| Flame alarm sensitivity | High, Low, Off | Flame detection sensitivity level. "Off" disables the alarm. |
| Flame alarm mode | Mode 1-4 | D-series detection mode preset. **D-series only.** |

### Buttons

| Entity | Description |
|--------|-------------|
| Pause job | Pause the current processing job |
| Resume job | Resume a paused job |
| Cancel job | Cancel and stop the current job |
| Home all axes | Move laser head to home position (all axes) |
| Home XY | Move laser head to XY home position |
| Home Z | Move laser head to Z home position |
| Home laser head | Move REST laser head back to (0, 0). **P2 / P2S / F1 / F1 Ultra only.** |
| Measure distance | Trigger an IR distance measurement. **P2 / P2S only.** |
| Quit LightBurn | Leave LightBurn standby mode. **D-series only.** |

### Binary Sensors

| Entity | Description |
|--------|-------------|
| Accessories | On when accessories are attached. Attributes list the connected accessories (Air Pump, Fire Extinguisher, Riser Base) with firmware versions. **S1 only.** |
| Alarm | Generic problem flag — on when the device reports any active alarm. **S1 only.** |
| Air cleaner running | AP2 air cleaner currently running. **S1 with AP2 only.** |
| Air cleaner connected | AP2 module slot in the accessories array is populated. **S1 with AP2 only.** |
| Air-Assist connected | Air-Assist V2 accessory connected. **M1 Ultra only.** |
| Cover | Cover / lid open detection. **F1 V2 (push) and REST cover models like P2 / P2S only.** |
| Machine lock | Machine lock state (LOCK device class — `on` = unlocked). **F1 V2 only.** |

### Update

| Entity | Description |
|--------|-------------|
| Firmware | Reports the installed and latest firmware version by querying the xTool cloud (re-checked on reconnect and every 6 hours). Install is disabled by default; enable **Enable firmware updates** in the integration options to arm it. |

### Cameras

| Entity | Description |
|--------|-------------|
| Overview camera | Wide-angle workspace camera. **P2 / P2S / F1 Ultra.** |
| Close-up camera | Detailed close-up camera. **P2 / P2S / F1 Ultra.** |
| Flame record | Snapshot of the most recent flame detection event. **F1 Ultra only.** |

## Device Information

The device page in Home Assistant shows:

- **Manufacturer:** xTool
- **Model:** e.g. xTool S1
- **Serial Number:** Device serial number
- **Firmware:** firmware version

## Using XCS alongside Home Assistant

The xTool Creative Space (XCS) desktop app and this integration can be used in parallel. When the XCS app is detected (it causes WebSocket disconnects), the integration automatically switches to **XCS Compatibility Mode**:

- Commands fall back to HTTP
- Entity states are preserved from the last successful poll
- Failed commands are prioritized in the next poll cycle (rotating command order)
- Periodic recovery attempts restore the WebSocket connection when XCS is closed

## Technical Details

This integration communicates with xTool devices using protocols discovered through reverse engineering of the xTool XCS Android app:

- **UDP port 20000** — Device discovery via broadcast (`{"requestId": <int>}`)
- **WebSocket port 8081** (S1) — Real-time G-code command/response with push state updates
- **HTTP port 8080** — System queries, file upload, command fallback
- **REST API port 8080** (F1/P2/M1) — JSON REST endpoints for device control

Commands use a G-code dialect (M-codes): `M222` (status), `M13` (light), `M340` (flame alarm), `M2003` (full device info), etc.

A complete reference of all M-codes, HTTP endpoints, JSON formats, status mappings, and the firmware-update cloud API is in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Multilanguage Support

This integration supports English and German.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
