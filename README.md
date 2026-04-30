# xTool Laser

Home Assistant integration for xTool laser cutters, engravers, fiber-laser welders and inkjet printers (S1, D1 family, F1 family, F2 family, M1 family, P-family, MetalFab, Apparel Printer, …).

This integration communicates directly with your xTool device over the local network using the reverse-engineered WebSocket and REST protocols. No cloud connection required.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/thecodingdad/ha-xtool)](https://github.com/thecodingdad/ha-xtool/releases)

## Features

- Full local control — no cloud, no xTool account required
- Monitor device status (idle, processing, paused, finished, error, ...)
- Control light brightness, buzzer, exhaust + cooling fans, safety toggles
- Configure air-assist post-run, exhaust post-run, sleep timeouts, fill-/IR-light auto-off, work-area limits
- Flame alarm sensitivity (high / low / off) and mode selection
- Job control buttons (pause, resume, cancel, home axes, reboot, time-sync)
- Laser position tracking (X/Y/Z), gyro/accelerometer (P2/P2S, F1 Ultra family, M1 Ultra, MetalFab), workhead identity + Z height (M1 Ultra)
- Lifetime statistics (working time, session count, standby time, laser module runtime)
- Laser module detection (type + power) and hardware type
- Attached accessories detection (air pump, fire extinguisher, riser base, Bluetooth dongle)
- AP2 air-cleaner sensors (filter remaining, particle dust sensors, speed)
- Water-cooling telemetry — temperature / flow / pump / line OK (F1 Ultra family fiber laser)
- Z-axis temperature + CPU fan (M1 Ultra), UV fire sensor (F1 Ultra family / F2 Ultra UV / M1 Ultra / P2S)
- Camera support for P2 / P2S / F1 Ultra family / F2 Ultra family / P3 / MetalFab (overview + close-up + flame record)
- Firmware update entity — checks the xTool cloud for new firmware, including changelog (install off by default, opt-in with confirmation)
- Optional power switch linking (smart plug control)
- Push state updates via WebSocket (S1, V2 firmware family, D-series) — minimal polling delay
- Automatic reconnect on network interruption
- Multi-model support with auto-detected protocol (V1 vs V2 firmware probed at setup)

## Prerequisites

- Home Assistant 2025.1.0 or newer
- xTool device on the same local network, connected via WiFi

## Supported Devices

The integration supports four protocol families that cover all current xTool models:

| Protocol | Models | Communication |
|----------|--------|---------------|
| WebSocket M-code | S1 | bidirectional WS (port 8081) + HTTP fallback (port 8080) |
| HTTP REST + status push WS | D1, D1 Pro, D1 Pro 2.0 | HTTP `/cmd` writes (port 8080) + read-only WS status push (port 8081) |
| REST API (V1 firmware) | F1, F1 Ultra, F1 Ultra V2 (GS003), F1 Lite (GS005), F2, F2 Ultra, F2 Ultra Single, F2 Ultra UV, M1, M1 Ultra, MetalFab (HJ003), P1, P2, P2S, P3, Apparel Printer (DT001) | HTTP REST (ports 8080 main / 8087 firmware / 8329 camera) |
| WS-V2 (V2 firmware ≥ 40.51) | F1, F1 Ultra, F1 Ultra V2 (GS003), F1 Lite (GS005), F2 family, M1 Ultra, P2S, P3, MetalFab, Apparel Printer | TLS WebSocket (port 28900) — three concurrent channels: instruction (JSON request/response + push events), file_stream (binary uploads), media_stream (camera frames) |

V1/V2 selection is automatic. At setup the integration probes port 28900; devices that answer use the WS-V2 family, devices that don't fall back to the legacy REST API on port 8080. Same physical device behind one config entry — re-add the device after a major firmware upgrade to switch families.

> **Note:** The integration was developed and tested with an xTool S1. Other models are supported based on reverse-engineering the xTool XCS Android app and the newer xTool Studio Windows app, but have not been tested with real hardware. Community feedback is welcome!

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

The available options are gated by the device's protocol family — only relevant fields appear.

| Option | Description |
|--------|-------------|
| Power switch | Link an existing switch entity (e.g. a smart plug) that controls the laser's power supply. When the switch is off, the status shows "Off" and entities become unavailable. |
| Polling interval | Main coordinator polling cadence in seconds (default 5). Lower = more responsive, higher network usage. |
| Enable firmware updates | _(only when the model has a known firmware ID)._ Off by default — the firmware update entity only reports whether an update is available. Enabling this option arms the install action. |
| Firmware-update check interval | _(only when firmware updates are supported.)_ How often the cloud is polled for new firmware (default 6 h). |
| AP2 air cleaner | **S1 only.** Opt-in toggle that adds the AP2 air-cleaner sensors (running / connected / speed / filter remaining / dust sensors) and starts polling the air-cleaner push frame. Leave off if you do not own an AP2. |
| AP2 air-cleaner polling interval | **S1 only.** How often the AP2 push frame is queried (default 30 s). |
| Lifetime-stats polling interval | **S1 only.** How often `M2008` is polled for working time / session count / standby / runtime (default 300 s). |
| Bluetooth-dongle polling interval | **S1 only.** How often `M9098` is polled for connected BLE accessories (default 60 s). |

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
| Last button event | Last physical button event (single / long / double press). **REST + WS-V2 families.** |
| Working mode | Current device working mode (cut / engrave / ...). **REST + WS-V2 families.** |
| Workhead | Detected workhead identity (laser / knife / inkjet / ...). **M1 Ultra.** |
| Workhead Z height | Workhead Z height offset in mm. **M1 Ultra.** |
| Z-axis temperature | Z-axis NTC temperature. **M1 Ultra.** |
| Water temperature / Water flow rate | Water-cooling loop sensors. **F1 Ultra fiber laser.** |
| Gyro X / Y / Z | 3-axis accelerometer. **F1 Ultra family / M1 Ultra / P2 / P2S / MetalFab.** |
| Purifier speed / filter remaining (pre / medium / carbon / dense carbon / HEPA) / sensor D / sensor S | AP2 air-cleaner telemetry. **S1 with AP2 only.** |

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
| Last job time | Last completed job duration from push frames. **WS-V2 family.** |
| Print tool type | Print tool kind reported by device. **REST family.** |
| Hardware type | Hardware revision string. **REST family.** |

### Light

| Entity | Description |
|--------|-------------|
| Fill light | Dimmable work light (0-100%) |

### Switches

| Entity | Description |
|--------|-------------|
| Buzzer / Beep | Enable/disable audio feedback. (S1 + REST family) |
| Move stop | Enable/disable emergency movement stop |
| Exhaust fan | Enable/disable smoke extraction fan |
| Power | Controls the linked smart plug (only when configured in options) |
| Tilt stop / Limit stop / Move stop (D-series) | Per-sensor safety toggles. **D-series only.** |
| IR LED close-up / IR LED global | Cover and global IR illumination. **P2 / P2S only.** |
| Cover lock | Cover digital lock control. **P2 / P2S only.** |
| Drawer check / Filter check / Purifier check | Safety enforcement toggles (require accessory present before job). **REST family.** |
| Purifier auto-continue | Keep purifier running after job ends. **REST family.** |
| Cooling fan | CPU + laser cooling fan toggle. **REST family.** |

### Numbers

| Entity | Range | Unit | Description |
|--------|-------|------|-------------|
| Air-assist post-run | 0-600 | seconds | Time the air assist stays on after a job ends |
| Exhaust post-run | 1-600 | seconds | Time the exhaust fan stays on after a job ends |
| Tilt threshold / Movement threshold | 0-255 | — | D-series sensor sensitivities |
| Camera exposure (overview / close-up) | 0-255 | — | Camera exposure values. **All REST family models with cameras (F1 family / F2 family / P2 / P2S / P3).** |
| Air-Assist gear (cut / engrave) | 0-4 | — | Default Air-Assist gear written to user config — applied to next job. **M1 Ultra only; only available when an Air-Assist accessory is attached.** |
| Purifier auto-off | 0-3600 | seconds | Air-purifier auto-off delay. **REST family.** |
| Sleep timeout | 0-3600 | seconds | Idle sleep timeout. **REST family.** |
| Sleep timeout (cover open) | 0-3600 | seconds | Idle sleep timeout while cover is open. **REST family.** |
| Fill-light auto-off | 0-3600 | seconds | Built-in fill light auto-off. **REST family.** |
| IR-light auto-off | 0-3600 | seconds | IR illumination auto-off. **REST family.** |
| Display brightness | 0-100 | % | Built-in display brightness. **F1 Ultra / F1 Ultra V2 only.** |
| Work area limit (left / right / up / down) | device-specific | mm | M311 work-area limits. **D-series only.** |

### Select

| Entity | Options | Description |
|--------|---------|-------------|
| Flame alarm sensitivity | High, Low, Off | Flame detection sensitivity level. "Off" disables the alarm. |
| Flame alarm mode | Mode 1-4 | D-series detection mode preset. **D-series only.** |
| Purifier speed | Off, Low, Medium, High | AP2 / external purifier speed. **REST family.** |
| Flame level | High, Low | Flame-level threshold. **REST family.** |
| Red-cross mode | Cross-laser pointer, Low-light mode | M97 red-cross laser mode. **D-series only.** |

### Buttons

| Entity | Description |
|--------|-------------|
| Pause job | Pause the current processing job |
| Resume job | Resume a paused job |
| Cancel job | Cancel and stop the current job |
| Home all axes | Move laser head to home position (all axes) |
| Home XY | Move laser head to XY home position |
| Home Z | Move laser head to Z home position |
| Home laser head | Move REST laser head back to (0, 0). **F1 / F1 Ultra family / F2 family / P2 / P2S / P3.** |
| Measure distance | Trigger an IR distance measurement. **P2 / P2S / P3.** |
| Quit LightBurn | Leave LightBurn standby mode. **D-series only.** |
| Reboot | Soft-reboot the device. **REST family.** |
| Sync time | Push HA's local time to the device. **REST family.** |

### Binary Sensors

| Entity | Description |
|--------|-------------|
| Accessories | On when accessories are attached. Attributes list connected accessories (Air Pump, Fire Extinguisher, Riser Base, Bluetooth dongle) with firmware versions. **S1 only.** |
| Alarm | Generic problem flag — on when the device reports any active alarm. **S1 only.** |
| XCS compatibility mode | Diagnostic flag — on when the integration detects the XCS desktop app holding the WebSocket and switches all writes to HTTP `/cmd`. **S1 only.** |
| Air cleaner running | AP2 air cleaner currently running. **S1 with AP2 only.** |
| Air cleaner connected | AP2 module slot in the accessories array is populated. **S1 with AP2 only.** |
| Air-Assist connected | Air-Assist V2 accessory connected. **M1 Ultra + WS-V2 family.** |
| Cover | Cover / lid open detection. **WS-V2 family (push), P2 / P2S / P3 / MetalFab.** |
| Machine lock | Machine lock state (LOCK device class — `on` = unlocked). **F1 family / F2 family / WS-V2.** |
| Drawer | Front-drawer position. **M1 Ultra / P2 / P2S / P3 / MetalFab.** |
| Cooling fan | Cooling fan running. **REST family.** |
| Exhaust fan | Smoke extraction running. **REST family.** |
| CPU fan | CPU fan running. **M1 / M1 Ultra.** |
| UV fire sensor | UV-based fire detection alarm. **F1 Ultra / F1 Ultra V2 / F2 Ultra UV / M1 Ultra / P2S.** |
| Water pump | Water-cooling pump running. **F1 Ultra family / P3.** |
| Water line | Water-cooling line OK. **F1 Ultra family / P3.** |

### Update

| Entity | Description |
|--------|-------------|
| Firmware | Reports the installed and latest firmware version by querying the xTool cloud (re-checked on reconnect and every 6 hours). The release notes (English title + description) from the cloud are shown as the changelog. Install is disabled by default; enable **Enable firmware updates** in the integration options to arm it. |

### Cameras

| Entity | Description |
|--------|-------------|
| Overview camera | Wide-angle workspace camera. **F1 Ultra / F1 Ultra V2 / F2 Ultra / F2 Ultra Single / F2 Ultra UV / P2 / P2S / P3 / MetalFab.** |
| Close-up camera | Detailed close-up camera. **F1 Ultra / F1 Ultra V2 / F2 Ultra / F2 Ultra Single / F2 Ultra UV / P2 / P2S / P3 / MetalFab.** |
| Flame record | Snapshot of the most recent flame detection event. **F1 Ultra / F1 Ultra V2.** |

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

The integration speaks four protocol families, all reverse-engineered from the xTool XCS Android app and the newer xTool Studio Windows app (the current primary source):

- **UDP port 20000** — device discovery via broadcast (`{"requestId": <int>}`)
- **WebSocket port 8081** (S1) — bidirectional G-code RPC + push state updates
- **HTTP port 8080** — S1 file upload + `/cmd` fallback; D-series + REST family main API
- **WebSocket port 8081** (D-series) — read-only status push (`ok:IDLE`, `err:flameCheck`, …)
- **HTTP port 8087** (REST family) — firmware handshake + flash
- **HTTP port 8329** (REST family) — camera snap + exposure
- **TLS WebSocket port 28900** (WS-V2 family, V2 firmware ≥ 40.51) — three concurrent channels (`function=instruction` JSON request/response + push events, `function=file_stream` binary uploads, `function=media_stream` camera frames). Replaces the legacy REST API on devices running V2 firmware

Commands use a G-code dialect (M-codes): `M222` (status), `M13` (light), `M340` (flame alarm), `M2003` (full device info), `M9098` (BLE accessories), etc.

The firmware-update entity hits the public xTool cloud API (`api.xtool.com`) on the `atomm` namespace using `xTool-*-firmware` content IDs.

A complete reference of all M-codes, HTTP endpoints, JSON formats, status mappings, capability flags per model, and the firmware-update cloud API is in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Multilanguage Support

This integration supports English and German.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
