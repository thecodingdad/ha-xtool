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
- Attached BT accessories as child devices (Smart Air Assist, SafetyPro AP2 / AP2 Max, SafetyPro IF2 / IF2 2.0, Fire Safety Set, ...)
- Camera support for P2 / P2S / F1 Ultra family / F2 family / P3 / MetalFab (overview + close-up or main + deep depending on model, plus flame record).
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
| Power switch | Link an existing switch entity (e.g. a smart plug) that controls the laser's power supply. When the switch is off the status sensor reports "Off" and **control entities** (buttons, switches, numbers, cameras) become unavailable. Read-only sensors keep their last-known value so dashboards stay populated. |
| Polling interval | Main coordinator polling cadence in seconds (default 5). Lower = more responsive, higher network usage. |
| Enable firmware updates | _(only when the model has a known firmware ID)._ Off by default — the firmware update entity only reports whether an update is available. Enabling this option arms the install action. |
| Firmware-update check interval | _(only when firmware updates are supported.)_ How often the cloud is polled for new firmware (default 6 h). |
| AP2 air cleaner | **S1 only.** Opt-in toggle that adds the AP2 air-cleaner sensors (running / connected / speed / filter remaining / dust sensors) and starts polling the air-cleaner push frame. Leave off if you do not own an AP2. |
| AP2 air-cleaner polling interval | **S1 only.** How often the AP2 push frame is queried (default 30 s). |
| Lifetime-stats polling interval | **S1 only.** How often `M2008` is polled for working time / session count / standby / runtime (default 300 s). |
| Accessory polling interval | **S1 only.** How often the accessory subsystem is refreshed (`M1098` slot-array walk for USB accessories like Air Pump and Fire Extinguisher. Default 60 s. |

## Entities

> **Note:** Not every entity is available on every model. The integration auto-detects the device model and connected accessories and only creates entities for supported features. The tables below are a reference for what each entity does — if an entity is missing, your model simply does not expose that feature.

### Sensor

| Entity | Description |
|---|---|
| Status | Off / Idle / Processing / Paused / Finished / Error / … Always available — shows "Off" when device is unreachable |
| Task ID | Active or last loaded job ID. Stays at the last value while idle (no reset on job end) |
| Task time | Job elapsed time (seconds). On V2 firmware only emitted at end of job via `/work/result WORK_FINISHED` push — no live update during a job |
| Last job time | Last completed job duration |
| Working mode | Current working mode (cut / engrave / knife / inkjet / …). REST V1 + D-series only — V2 firmware uses this field as a `HANDLE`/`NORMAL` enum surfaced through the *Stops when moved* switch instead |
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
| Riser base | **S1 only.** Identifies which riser base is mounted (reads `M1098` slot, mapped through `RISER_BASE_NAMES`). |

### Binary Sensor

| Entity | Description |
|---|---|
| Cover | Cover / lid open detection |
| Drawer | Front-drawer position |
| Safety key | USB safety-key presence (PLUG device class — `on` = plugged in / armed, `off` = unplugged / lockout). Sourced from `/peripheral/machine_lock` and the `/machine_lock/status` push |
| Air-Assist running | Air is actually flowing (laser commanded `A=1` **and** `gear > 0`) |
| Air-Assist connected | Air-Assist hardware is plugged into the laser (raw `A=1` flag from `M15`) |
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
| XCS compatibility mode | On when the XCS desktop app holds the WebSocket and writes are routed via HTTP `/cmd` |

Connected BT accessories surface as their own **child devices** hanging off the laser (Smart Air Assist, SafetyPro AP2 / AP2 Max, SafetyPro IF2 / IF2 2.0, Fire Safety Set, …) and carry their own sensor / binary-sensor / switch / number / select set — gear, running flag, filter wear, connection status, post-run timeout, buzzer toggle, and so on. See the laser device page under **Devices** to navigate into the linked accessories.

### Switch

| Entity | Description |
|---|---|
| Power | Linked smart plug (only when configured in integration options) |
| Buzzer reminders / Beep | Audio feedback enable |
| Move stop | Emergency-movement-stop toggle |
| Exhaust fan | Manual smoke-extraction fan toggle |
| Cooling fan | Manual cooling-fan toggle |
| Purifier check / Drawer check | Safety-enforcement toggles — require accessory present before starting a job |
| Purifier auto-continue | Keep purifier running after job ends |
| Red dot / IR LED close-up | Red-dot pointer (`mdi:laser-pointer`) plus, on V1 dual-LED models, the close-up IR LED |
| Cover lock | Digital cover lock |
| Tilt stop / Limit stop | Per-sensor safety toggles |
| Flame alarm | Flame-alarm config toggle (single on/off — no separate sensitivity Select) |
| Stops when enclosure opened | Cover-safety enforcement — pauses the job when the lid opens mid-run |
| Stops when moved | V2 only. Engages when the device is moved mid-job (backed by the `workingMode` enum: `HANDLE` = on, `NORMAL` = off) |
| Device sleep | V2 only (F1 / F2 family). Toggles `autoSleepEnable` so the device powers down on idle |

### Number

| Entity | Description |
|---|---|
| Air-assist post-run | Time air-assist stays on after a job ends (0-600 s) |
| Exhaust post-run | Time exhaust fan stays on after a job ends (1-600 s) |
| Sleep timeout | Idle sleep timeout (0-3600 s) |
| Sleep timeout (cover open) | Idle sleep timeout while cover is open (0-3600 s) |
| IR-light auto-off | IR illumination auto-off (0-3600 s) |
| Purifier auto-off | Air-purifier auto-off delay (0-3600 s) |
| Camera exposure (overview / close-up) | Camera exposure values (0-255) |
| Air-Assist gear (cut / engrave) | Default Air-Assist gear written to user config — applied to next job (0-4). Requires Air-Assist accessory |
| Display brightness | Built-in touchscreen brightness (0-100 %) |
| Tilt threshold / Movement threshold | Tilt- and motion-sensor sensitivities (0-255) |
| Work area limit (left / right / up / down) | M311 work-area limits (mm) |

### Select

| Entity | Description |
|---|---|
| Purifier speed | Off / Low / Medium / High — AP2 / external purifier speed |
| Flame alarm mode | Mode 1-4 — flame-detection mode preset |
| Red-cross mode | Cross-laser pointer / Low-light mode |

### Light

| Entity | Description |
|---|---|
| Fill light | Dimmable work light (0-100 %). Single entity on most models |
| Fill light front / Fill light back | F2 family (dual-LED enclosure) — separate front/back channels driven by `fillLightBrightFront` / `fillLightBrightBack` |

### Camera

| Entity | Description |
|---|---|
| Camera | Single workspace camera (single-camera V2 models such as F1 Ultra V2). Streams live MJPEG with snapshot fallback |
| Main camera | Wide-angle workspace camera (F2 family + MetalFab on V2 firmware). Streams live MJPEG with snapshot fallback |
| Deep camera | Close-up / depth camera (F2 family + MetalFab on V2 firmware). Streams live MJPEG with snapshot fallback |
| Overview camera | Wide-angle workspace camera (P-family + V1-firmware dual-camera devices) |
| Close-up camera | Detail camera (P-family + V1-firmware dual-camera devices) |
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
| Z-axis homing | F2 Ultra UV. Triggers Studio's z-axis homing routine |
| Measure distance | Trigger an IR distance measurement |
| Reboot | Soft reboot |
| Sync time | Push HA local time to device |
| Quit LightBurn | Leave LightBurn standby mode |

### Update

| Entity | Description |
|---|---|
| Firmware | Installed + latest firmware version via the xTool cloud (re-checked on reconnect and every 6 h. Release notes from the cloud are shown as the changelog. Install is disabled by default; enable **Enable firmware updates** in the integration options to arm it. |

### Event

Transient-event entities — fire once on edge transitions (rather than holding state) so they can be used as automation triggers without polling the Status sensor or template-history.

| Entity | Event types | Description |
|---|---|---|
| Button | `short_press`, `long_press`, `double_press` | Physical front-panel button press. Source: WS-V2 push (`/button/status`) or REST poll diff. Includes a `raw_type` attribute so unrecognised firmware labels are still inspectable |
| Job | `started`, `paused`, `resumed`, `cancelled`, `finished`, `framing_started`, `framing_finished` | Job-lifecycle transitions derived from Status sensor edges. `task_id` and (where available) job `duration` are exposed as event attributes |
| Error | `limit`, `laser_control`, `laser_module`, `tilt`, `moving`, `emergency_stop`, `temperature`, `gyro`, `laser_head_fault`, `z_axis_fault`, `u_axis_fault`, `conveyor_fault`, `board_fault`, `camera_fault`, `dongle_fault`, `udisk_fault`, `machine_lock_md_fault` | Error-state transitions. `tilt` / `moving` are D-series only; `emergency_stop` plus the `*_fault` and hardware-alarm types are V2 only (driven by per-subsystem `/.../alarm` pushes — `/emergency/status` on MetalFab and `/emergency_stop/status` on the F-series are both routed to `emergency_stop`) |
| Fire warning | `triggered`, `cleared` | Flame-detector edge — separate entity so safety automations can target it directly. Source: `M340` push (S1), `ERROR_FIRE_WARNING` status edge (REST + D-series), or `state.alarm_present` / `/v1/device/alarms` / `/fire/alarm` push (WS-V2) |

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
- **BT-accessory tunnel** (`uart485` + F0F7 envelope) — wraps BT-accessory M-codes (`M9033` purifier, `M9082` duct fan, `M9098` dongle, …) for transport over the laser's main API. The endpoint is `/passthrough` (REST + D-series, port 8080) or `/v1/parts/control` (WS-V2 instruction channel). **The S1 does not have this tunnel** — its BT accessories are reached over the raw M-code WS

The firmware-update entity hits the public xTool cloud API (`api.xtool.com`) on the `atomm` namespace using `xTool-*-firmware` content IDs.

A complete reference of all M-codes, HTTP endpoints, JSON formats, status mappings, capability flags per model, and the firmware-update cloud API is in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Multilanguage Support

This integration supports English and German.

## Disclaimer

This is an **independent community project**. It is not affiliated with, endorsed by, sponsored by, or otherwise associated with xTool, Makeblock, or any of their employees, contractors, or partners. All product names, trademarks, and registered trademarks (including "xTool", "xTool Studio", "XCS", and the model names referenced in this README) are the property of their respective owners and are used here for descriptive purposes only.

The integration is provided **as-is, without warranty of any kind**. It relies on reverse-engineered protocols that may change without notice in future firmware revisions. Operating a high-power laser, fiber-laser welder, or any of the supported devices through Home Assistant carries real-world safety risks (fire, blindness, burns, electrical hazards). You are responsible for following xTool's official safety guidelines, supervising every job, and ensuring proper ventilation and fire-suppression measures are in place. The author accepts **no liability** for damages, injuries, data loss, voided warranties, or any other consequences arising from the use of this integration. **Use at your own risk.**

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
