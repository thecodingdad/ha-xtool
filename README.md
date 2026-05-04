# xTool Laser

Home Assistant integration for xTool laser cutters, engravers, fiber-laser welders and inkjet printers (S1, D1 family, F1 family, F2 family, M1 family, P-family, MetalFab, Apparel Printer, …).

This integration communicates directly with your xTool device over the local network using the reverse-engineered WebSocket and REST protocols. No cloud connection required.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/thecodingdad/ha-xtool)](https://github.com/thecodingdad/ha-xtool/releases)

> ⚠️ **This is an independent community project and is not affiliated with or endorsed by xTool. The integration is provided as-is, without any warranty.** Operating a laser cutter, engraver or fiber-laser welder through Home Assistant carries real-world safety risks (fire, blindness, burns, electrical hazards) — automated control over a high-power laser is *your* responsibility. Use at your own risk; see the full [Disclaimer](#disclaimer) for details.

## Features

- Full local control — no cloud, no xTool account required
- Monitor device status (idle, processing, paused, finished, error, ...)
- Control light brightness, buzzer, exhaust + cooling fans, safety toggles
- Job control buttons (pause, resume, cancel, ...)
- Monitor various sensors (laser position, gyro/accelerometer, ...)
- Lifetime statistics (working time, session count, standby time, laser module runtime)
- Attached accessories detection (air pump, fire extinguisher, riser base, Bluetooth dongle)
- Camera support for P2 / P2S / F1 Ultra family / F2 Ultra family / P3 / MetalFab (overview + close-up + flame record)
- Firmware update entity — checks the xTool cloud for new firmware, including changelog (install off by default, opt-in with confirmation)
- Optional power switch linking (smart plug control)
- Automatic reconnect on network interruption
- Multi-model support with auto-detected protocol

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
| WS-V2 (V2 firmware) | F1, F1 Ultra, F1 Ultra V2 (GS003), F1 Lite (GS005), F2 family, M1 Ultra, P2S, P3, MetalFab, Apparel Printer | TLS WebSocket (port 28900) — three concurrent channels: instruction (JSON request/response + push events), file_stream (binary uploads), media_stream (camera frames) |

V1/V2 selection is automatic. UDP discovery tags each device as V1 or V2 (legacy plain probe vs. encrypted multicast).

> **Note:** The integration was developed and tested with an xTool S1. Other models are supported based on reverse-engineering the xTool Studio Windows app, but have not been tested with real hardware. Community feedback is welcome!

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

> **Note:** Not every entity is available on every model. The integration auto-detects the device model and connected accessories and only creates entities for supported features. The tables below are a reference for what each entity does — if an entity is missing, your model simply does not expose that feature.

### Sensor

| Entity | Description |
|---|---|
| Status | Off / Idle / Processing / Paused / Finished / Error / … Always available — shows "Off" when device is unreachable |
| Task ID | Active or last loaded job ID. Stays at the last value while idle (no reset on job end) |
| Task time | Current job elapsed time (seconds) |
| Last job time | Last completed job duration |
| Working mode | Current working mode (cut / engrave / knife / inkjet / …) |
| Last button event | Last physical button event (single / long / double press) |
| Working time | Lifetime working hours |
| Session count | Lifetime job-start count |
| Standby time | Lifetime standby hours |
| Tool runtime | Lifetime laser-module runtime |
| Laser position X / Y / Z | Laser-head position in mm |
| Fire level | Current flame-detection level reported by device |
| Air-Assist level | Current Air-Assist gear (0-4) |
| Workhead | Detected workhead identity (laser / knife / inkjet / …) |
| Workhead Z height | Workhead Z-height offset in mm |
| Z-axis temperature | Z-axis NTC temperature |
| Water temperature / Water flow rate | Water-cooling loop sensors |
| Gyro X / Y / Z | 3-axis accelerometer |
| Print tool type | Print-tool kind reported by device |
| Hardware type | Hardware revision string |
| AP2 filter (pre / medium / carbon / dense carbon / HEPA) | Remaining filter capacity (requires AP2 air cleaner) |
| AP2 sensor D / AP2 sensor S | Particle sensors (requires AP2 air cleaner) |
| Purifier speed (sensor) | Current AP2 speed reading (requires AP2 air cleaner) |

#### Diagnostic Sensors

| Entity | Description |
|---|---|
| IP address | Device IP address |
| Laser power | Laser-module power in watts |
| Laser module | Laser-module type (e.g. "Diode", "IR") |
| SD card | SD-card inserted / not inserted |
| Workspace size | Build dimensions (e.g. "498 × 330 × 58 mm") |
| Active connections | Number of WebSocket clients currently connected to the laser (HA + XCS app + …). Useful to spot when the XCS app has kicked the integration |
| Origin offset X / Y | Last set work-area origin offset |
| Last distance | Last IR-distance measurement result |

### Binary Sensor

| Entity | Description |
|---|---|
| Cover | Cover / lid open detection |
| Drawer | Front-drawer position |
| Machine lock | Machine-lock state (LOCK device class — `on` = unlocked) |
| Air-Assist connected | Air-Assist V2 BLE accessory paired |
| Cooling fan | Cooling fan currently running |
| Exhaust fan | Smoke-extraction fan currently running |
| CPU fan | CPU cooling fan currently running |
| UV fire sensor | UV-based flame-detector trip |
| Water pump | Water-cooling pump running |
| Water line | Water-cooling line OK |
| Flame alarm enabled | Flame-alarm config flag |
| Beep enabled | Beep config flag |
| Gap check enabled | Cover-safety enforcement enabled |
| Lock check enabled | Machine-lock safety enforcement enabled |
| Alarm | Generic problem flag — on when device reports any active alarm |
| Accessories | On when accessories are attached. Attributes list connected accessories (Air Pump, Fire Extinguisher, Riser Base, Bluetooth dongle) with firmware versions |
| Air cleaner running / Air cleaner connected | AP2 module state (requires AP2 air cleaner) |
| XCS compatibility mode | On when the XCS desktop app holds the WebSocket and writes are routed via HTTP `/cmd` |

### Switch

| Entity | Description |
|---|---|
| Power | Linked smart plug (only when configured in integration options) |
| Beep / Buzzer | Audio feedback enable |
| Move stop | Emergency-movement-stop toggle |
| Exhaust fan | Manual smoke-extraction fan toggle |
| Cooling fan | Manual cooling-fan toggle |
| Filter check / Purifier check / Drawer check | Safety-enforcement toggles — require accessory present before starting a job |
| Purifier auto-continue | Keep purifier running after job ends |
| IR LED close-up / IR LED global | Cover and global IR illumination |
| Cover lock | Digital cover lock |
| Tilt stop / Limit stop | Per-sensor safety toggles |
| Flame alarm (config) | Flame-alarm config toggle |
| Beep (config) | Beep config toggle |
| Gap check | Cover-safety enforcement |
| Lock check | Machine-lock safety enforcement |

### Number

| Entity | Description |
|---|---|
| Air-assist post-run | Time air-assist stays on after a job ends (0-600 s) |
| Exhaust post-run | Time exhaust fan stays on after a job ends (1-600 s) |
| Sleep timeout | Idle sleep timeout (0-3600 s) |
| Sleep timeout (cover open) | Idle sleep timeout while cover is open (0-3600 s) |
| Fill-light auto-off | Built-in fill light auto-off (0-3600 s) |
| IR-light auto-off | IR illumination auto-off (0-3600 s) |
| Purifier auto-off | Air-purifier auto-off delay (0-3600 s) |
| Camera exposure (overview / close-up) | Camera exposure values (0-255) |
| Air-Assist gear (cut / engrave) | Default Air-Assist gear written to user config — applied to next job (0-4). Requires Air-Assist accessory |
| Display brightness | Built-in touchscreen brightness (0-100 %) |
| Fire detection level | Flame-detector threshold (0-255) |
| Tilt threshold / Movement threshold | Tilt- and motion-sensor sensitivities (0-255) |
| Work area limit (left / right / up / down) | M311 work-area limits (mm) |

### Select

| Entity | Description |
|---|---|
| Flame alarm sensitivity | High / Low / Off — flame-detector level. "Off" disables the alarm |
| Flame level | High / Low — flame-level threshold |
| Purifier speed | Off / Low / Medium / High — AP2 / external purifier speed |
| Flame alarm mode | Mode 1-4 — flame-detection mode preset |
| Red-cross mode | Cross-laser pointer / Low-light mode |

### Light

| Entity | Description |
|---|---|
| Fill light | Dimmable work light (0-100 %) |

### Camera

| Entity | Description |
|---|---|
| Overview camera | Wide-angle workspace camera (snapshot on V2 firmware) |
| Close-up camera | Detail camera (snapshot on V2 firmware) |
| Flame record | Snapshot of the most recent flame-detection event |

### Button

| Entity | Description |
|---|---|
| Pause job | Pause the current processing job |
| Resume job | Resume a paused job |
| Cancel job | Cancel and stop the current job |
| Home all axes | Move laser head to home position (all axes) |
| Home XY | Move laser head to XY home |
| Home Z | Move laser head to Z home |
| Home laser head | Move laser head back to (0, 0) |
| Measure distance | Trigger an IR distance measurement |
| Reboot | Soft reboot |
| Sync time | Push HA local time to device |
| Quit LightBurn | Leave LightBurn standby mode |

### Update

| Entity | Description |
|---|---|
| Firmware | Installed + latest firmware version via the xTool cloud (re-checked on reconnect and every 6 h). Release notes from the cloud are shown as the changelog. Install is disabled by default; enable **Enable firmware updates** in the integration options to arm it |

## Device Information

The device page in Home Assistant shows:

- **Manufacturer:** xTool
- **Model:** e.g. xTool S1
- **Serial Number:** Device serial number
- **Firmware:** firmware version

## Using xTool studio alongside Home Assistant

When running xTool Studio app, formerly xTool Creative Space (XCS), and this integration in parallel, it can cause WebSocket disconnects, which in turn can cause delayed entity updates.

## Technical Details

The integration speaks four protocol families, all reverse-engineered from the xTool XCS Android app and the newer xTool Studio Windows app (the current primary source):

- **UDP port 20000** — device discovery via broadcast (`{"requestId": <int>}`)
- **WebSocket port 8081** (S1) — bidirectional G-code RPC + push state updates
- **HTTP port 8080** — S1 file upload + `/cmd` fallback; D-series + REST family main API
- **WebSocket port 8081** (D-series) — read-only status push (`ok:IDLE`, `err:flameCheck`, …)
- **HTTP port 8087** (REST family) — firmware handshake + flash
- **HTTP port 8329** (REST family) — camera snap + exposure
- **TLS WebSocket port 28900** (WS-V2 family, V2 firmware ≥ 40.51) — three concurrent channels (`function=instruction` JSON request/response + push events, `function=file_stream` binary uploads, `function=media_stream` camera frames). Replaces the legacy REST API on devices running V2 firmware

The firmware-update entity hits the public xTool cloud API (`api.xtool.com`) on the `atomm` namespace using `xTool-*-firmware` content IDs.

A complete reference of all M-codes, HTTP endpoints, JSON formats, status mappings, capability flags per model, and the firmware-update cloud API is in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Multilanguage Support

This integration supports English and German.

## Disclaimer

This is an **independent community project**. It is not affiliated with, endorsed by, sponsored by, or otherwise associated with xTool, Makeblock, or any of their employees, contractors, or partners. All product names, trademarks, and registered trademarks (including "xTool", "xTool Studio", "XCS", and the model names referenced in this README) are the property of their respective owners and are used here for descriptive purposes only.

The integration is provided **as-is, without warranty of any kind**. It relies on reverse-engineered protocols that may change without notice in future firmware revisions. Operating a high-power laser, fiber-laser welder, or any of the supported devices through Home Assistant carries real-world safety risks (fire, blindness, burns, electrical hazards). You are responsible for following xTool's official safety guidelines, supervising every job, and ensuring proper ventilation and fire-suppression measures are in place. The author accepts **no liability** for damages, injuries, data loss, voided warranties, or any other consequences arising from the use of this integration. **Use at your own risk.**

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
