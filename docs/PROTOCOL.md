# xTool Device Protocols

This document describes the network protocols used by xTool laser cutters,
based on three sources:

1. Live probes against an xTool S1 (the only hardware available to me).
2. Reverse engineering of the xTool XCS Android app — both the
   per-model JavaScript extension modules under
   `assets/exts/<model>/index.js` and the Java/Kotlin DEX files. **Note:**
   the APK uses Pairip protection (encrypted DEX blobs decrypted at
   runtime by `libpairipcore.so`); native protocol code is therefore
   not visible in standard decompiler output. The JS extension layer
   does expose the HTTP REST endpoints used by every model.
3. Cross-checks against the community integrations
   [Doormat1/XTool_D1_HA](https://github.com/Doormat1/XTool_D1_HA),
   [BassXT/xtool](https://github.com/BassXT/xtool), and the
   [1RandomDev/xTool-Connect protocol doc](https://github.com/1RandomDev/xTool-Connect/blob/master/XTOOL_PROTOCOL.md)
   — which were built from packet captures, the most reliable source
   for the bits Pairip hides.

The integration speaks four different families depending on the device
model:

| Family | Models | Transport | Port(s) |
|---|---|---|---|
| `ws_mcode` | S1 | WebSocket G-code RPC + HTTP fallback | 8081 (WS), 8080 (HTTP) |
| `d_series` | D1, D1 Pro, D1 Pro 2.0 | HTTP REST + status-push WebSocket | 8080 (HTTP), 8081 (WS) |
| `rest` | F1, F1 Ultra, F1 Lite, M1, M1 Ultra, P1, P2, P2S | HTTP REST (JSON) | 8080 |
| `f1_v2` | F1 firmware 40.51+ | TLS WebSocket (listener-only) | 28900 (wss) |

WebSocket usage notes:

- **S1**: bidirectional G-code dialect — both sends commands and
  receives push frames over WS.
- **D-series**: WebSocket is read-only — only status events
  (e.g. `ok:IDLE`, `err:flameCheck`). All writes happen via HTTP.
- **F1 V2**: TLS WebSocket on a custom port with a hand-shaken JSON
  push channel. No documented command channel — listener only.

All four are local-only — no cloud dependency for runtime operation.
(The cloud is only contacted by the firmware-update entity.)

### Capability matrix

Per-model capability flags on `XtoolDeviceModel` gate the entities each
device exposes. Live values are populated by the matching protocol;
unsupported models simply do not register the entity.

| Capability | S1 | D-series | P2/P2S | F1 / F1Ultra | M1 / M1U | F1 V2 |
|---|---|---|---|---|---|---|
| `has_flame_alarm` | yes | yes | yes | yes | yes | yes (push) |
| `has_smoking_fan` | yes | — | — | — | — | — |
| `has_move_stop` (S1 M318 only) | yes | — | — | — | — | — |
| `has_tilt_sensor` | — | yes | — | — | — | — |
| `has_moving_sensor` | — | yes | — | — | — | — |
| `has_limit_switch` | — | yes | — | — | — | — |
| `has_lid_sensor` (cover binary_sensor) | — | — | yes | — | — | yes |
| `has_machine_lock` | — | — | — | — | — | yes |
| `has_ir_led` | — | — | yes | — | — | — |
| `has_digital_lock` | — | — | yes | — | — | — |
| `has_distance_measure` | — | — | yes | — | — | — |
| `has_camera_exposure` | — | — | yes | yes | — | — |
| `has_fire_record` | — | — | — | F1U only | — | — |
| `has_laser_head_position` | — | — | yes | yes | — | — |
| `has_fill_light` (S1 M13) | yes | — | — | — | — | — |
| `has_fill_light_rest` | — | — | yes | yes | yes | — |
| `has_camera` (overview + close-up) | — | — | yes | F1U only | — | — |
| `has_purifier_timeout` | — | — | — | — | — | yes |
| `has_z_axis` (Z home + axis sensor) | yes | — | yes | — | yes | — |

## Discovery — UDP broadcast, port 20000

Devices listen on UDP/20000 for a JSON probe and reply with their identity.

Request (broadcast to `255.255.255.255:20000`):

```json
{"requestId": <random_int>}
```

Reply (unicast back from device):

```json
{"requestId": <echo>, "ip": "192.168.x.x", "name": "xTool S1", "version": "V40.32.013.2224.01"}
```

Implemented in `discovery.py`. The integration does this scan during the
config flow's "Add Integration" step.

---

## WS M-code protocol (S1)

The S1 exposes:

- WebSocket on port 8081 — bidirectional G-code dialect
- HTTP on port 8080 — system queries, firmware upload, command fallback

### WebSocket framing

Each frame is one line of ASCII text terminated with `\n`. Requests are
M-codes (e.g. `M222`); replies start with the same M-code echo. Push frames
arrive unprompted whenever device state changes.

Push frames currently handled (cached in `_push_state`):

- `M222 S{n}` — work-state changes
- `M810 "<name>"` — job filename changes
- `M340 A{n}` — alarm state changes
- `M313 X{} Y{} Z{}` — Z-probe readings
- `M15 A{n} S{n}` — air assist + light active

Sending `M2211` triggers the device to push a full state burst (all M-codes
listed above). The integration sends this once on every WebSocket connect
to refresh entities cheaply without paying for a full `M2003` round-trip.

### XCS Compatibility Mode

The XCS desktop app holds the WebSocket exclusively — when it connects, the
device kicks our WS. The integration detects this:

- ≥ 3 disconnects within 30 s while session was < 10 s long ⇒ enter
  XCS Compatibility Mode
- in XCS mode all writes go via HTTP POST `/cmd`
- a recovery probe runs every 60 s — two clean status queries in a row exit
  the mode

Cached state is preserved so entities don't go "Unavailable" while XCS is
running.

### M-code reference (S1)

Conventions: `{x}` = integer, `{x.y}` = float, `"…"` = quoted string.
Codes marked **(WS-only)** do not work via HTTP `/cmd`.

#### Queries (used by integration)

| Code | Response | Meaning |
|---|---|---|
| `M222` | `M222 S{n}` + push burst of state | Work state code (see status table below) |
| `M2003` | JSON: `{"M310":..., "M100":..., "M116":..., "M99":..., "M1199":..., "M2099":..., "M1098":[...]}` | Full device info dump |
| `M100` | `M100 "<name>"` | Device name |
| `M99` | `M99 V<x>.<y>...` | Main MCU firmware version |
| `M1199` | (in M2003 only) | Laser MCU firmware version |
| `M2099` | `M2099 V<x>.<y>... B<n>` | ESP32 / Wi-Fi firmware version |
| `M310` | `M310 "<serial>"` | Serial number |
| `M2002` | `M2002 "<ip>"` | Device IP |
| `M223` | `M223 X<mm> Y<mm> Z<mm>` | Workspace dimensions (S1: `498 × 330 × 58`) |
| `M116` | `M116 X<type> Y<watts> B<producer> P<process_type> L<laser_tube>` | Laser module info |
| `M27` | `M27 X<mm> Y<mm> Z<mm> U<mm>` | Current head position (int) |
| `M105` | `M105 X<mm.mm>Y<mm.mm>Z<mm.mm>` | Current position (float) |
| `M303` | (similar) | Laser coordinates |
| `M13` | `M13 A<0–100> B<0–100>` | Fill light brightness (A/B channels) |
| `M15` | `M15 A<0/1> S<0–4>` | Light active + air-assist gear |
| `M340` | `M340 A<0/1/2>` | Flame alarm sensitivity (0=high, 1=low, 2=off) |
| `M343` | `M343 S<n>` | Fire-detection level |
| `M7` | `M7 S<0/1> N<0/1> D<seconds>` | Smoking fan state |
| `M21` | `M21 S<0/1>` | Buzzer state |
| `M318` | `M318 N<0/1>` | Move stop state |
| `M1099` | `M1099 T<seconds>` | Air-assist close delay |
| `M810` | `M810 "<filename>"` | Current job filename |
| `M815` | `M815 T<seconds>` | Job time |
| `M321` | `M321 S<0/1>` | SD card present |
| `M362` | `M362 S<0/1>` | "xTouch" connected — refers to S1's **built-in** 3.5" touch panel, not an accessory; in practice always `S1`. Constant retained as documentation; no entity. |
| `M1098` | `M1098 "<v0>","<v1>",...` | Accessories with firmware versions (10-element array) |
| `M54` | `M54 T<0/1/2>` | Riser base / heightening kit |
| `M2008 A1` | `M2008 A<work_s> B<jobs> C<standby_s> D<runtime_s>` | Lifetime statistics. **Bare `M2008` returns nothing — needs `A1` (or any single param)** |

#### Control (used by integration)

| Code | Effect |
|---|---|
| `M22 S0` | Resume job |
| `M22 S1` | Pause job |
| `M22 S3` | Enter firmware-upgrade mode |
| `M108` | Cancel job |
| `M111 S2` / `S3` / `S7` | Home Z / XY / all axes |
| `M340 A<0/1/2>` | Set flame-alarm sensitivity |
| `M343 S<n>` | Set fire level |
| `M21 S<0/1>` | Beeper on/off |
| `M318 N<0/1>` | Move stop on/off |
| `M7 N<0/1> D<seconds>` | Smoking fan on/off + duration |
| `M1099 T<seconds>` | Set air-assist close delay |
| `M13 A<0–100> B<0–100>` | Fill light brightness |
| `M2211` | Trigger full-state push (cheap refresh) |

#### M222 work-state codes

| Code | Status | Notes |
|---|---|---|
| 0 | initializing | |
| 1, 3 | idle | |
| 2 | wifi_setup | Soft-bricked into setup mode if accidentally triggered |
| 4 | error_limit | |
| 7, 22 | error_laser_module | |
| 9, 20 | error_limit | also fire-alarm trigger |
| 10 | measuring | |
| 11 | frame_ready | |
| 12 | framing | |
| 13 | processing_ready | |
| 14 | processing | |
| 15 | paused | |
| 16 | firmware_update | |
| 17 | sleeping | |
| 18 | cancelling | |
| 19 | finished | |
| 21 | error_laser_control | |
| 24 | measure_area | |
| (TBD) | error_fire_warning | Stage-1 flame detect (firmware logs `fire first happened alarm`) — exact S-code unconfirmed |

#### Codes seen in firmware but not used by integration

| Code | Format | Likely meaning |
|---|---|---|
| `M105` | `X<mm.mm>Y<mm.mm>Z<mm.mm>` | Float position (alt to M27) |
| `M223` | `X<mm> Y<mm> Z<mm>` | Workspace dims (now used) |
| `M307` | `X<n> Y<n>` | Steps/mm or motion config |
| `M315` | `N<float>` | Sensor reading |
| `M319` | `X<n> Y<n> Z<n>` | Origin offset |
| `M326` | `N<n>` | State |
| `M346` | `S<n>` | State |
| `M365` | `A<f> B<f>` | Calibration values |
| `M370` | `N<n>` | State |
| `M535 S1` | `M535 U<float>` | Voltage / sensor |
| `M2005` | `S<n>` | Counter (uptime?) |
| `M2009` | `A<n> B<n> C<n> D<n> E<n>` | Multi-state |
| `M2033`, `M2036`, `M2109` | `S<n>` | Settings |

#### Codes verified dangerous — DO NOT SEND

| Code | Effect |
|---|---|
| `M341 S1` | Sends device into `wifi_setup` state (must power-cycle) |
| `M9006 A1` | Crashed WebSocket / forced reboot |
| `M120 A1.1`, `M2810` | Suspicious responses, avoid |

### HTTP endpoints (S1, port 8080)

#### `POST /cmd` — fire-and-forget M-code execution

Used by XCS Compatibility Mode for writes only. Body is the raw M-code
text. Response is always `{"result":"ok"}` regardless of what the M-code
actually did. **Replies (state values) come back via WebSocket push frames,
never on the HTTP response.** Don't use this for queries.

#### `GET /system?action=<name>`

| `action` | Response | Used? |
|---|---|---|
| `version` | ESP32/Wi-Fi firmware version (NOT main MCU — same as M2099) | yes (only as fallback) |
| `socket_conn_num` | active WS connection count | yes |
| `get_upgrade_progress` | `{"curr_progress":"<n>","total_progress":"<n>"}` | **yes** (real flash progress during install) |
| `get_dev_name` | (forwards to WS, garbled) | no |

Other `get_*` actions return empty.

#### Other endpoints

| Path | Method | Purpose | Used? |
|---|---|---|---|
| `/index.html`, `/favicon.ico` | GET | Web UI | no |
| `/burn` | POST multipart | Multi-board firmware flash with `burnType` + `M22 S3` prelude | **yes** (S1) |
| `/upgrade` | POST multipart / GET HTML | Single-blob firmware upload (alternative to `/burn`) | **yes** (REST models, fallback path) |
| `/upload`, `/gcode/*`, `/delete/gcode/*`, `/frame.gcode`, `/tmp.gcode` | POST/DELETE | Job file workflow — upload G-code, set frame, run | no (not implemented yet) |
| `/peripherals`, `/parts`, `/system` (no `action`) | GET | Returns `{"result":"ok"}` — body format unknown | no |
| `/net/get_ap_list`, `/net/set_wifi`, `/net/setWifi`, `/net/wifi_mode` | GET/POST | Wi-Fi reconfiguration | no (risky) |
| `/dev/console`, `/dev/uart`, `/dev/secondary` | n/a | Internal device file paths | no |

---

## D-series protocol (D1 / D1 Pro / D1 Pro 2.0)

Implemented in `d_series_protocol.py`.

### HTTP REST API on port 8080

| Endpoint | Method | Returns / Effect |
|---|---|---|
| `/ping` | GET | `{"result":"ok"}` — liveness check |
| `/getmachinetype` | GET | `{"result":"ok","type":"xTool D1Pro"}` |
| `/getlaserpowertype` | GET | `{"result":"ok","power":10}` |
| `/getlaserpowerinfo` | GET | `{"result":"ok","type":0,"power":10}` (type: 0=diode, 1=infrared) |
| `/peripherystatus` | GET | sdCard + safety flags + thresholds + flame sensitivity (see below) |
| `/progress` | GET | `{"progress":<float>,"working":<ms>,"line":<n>}` |
| `/system?action=mac` | GET | MAC address |
| `/system?action=version` | GET | `{"sn":"...","version":"V40.31.006.01 B2"}` |
| `/system?action=get_working_sta` | GET | `{"working":"0"}` (`0` idle, `1` API job, `2` button job) |
| `/system?action=offset` | GET | `{x,y}` work-area offset |
| `/system?action=get_dev_name` / `set_dev_name&name=<n>` | GET | Read or write user-set device name |
| `/system?action=setLimitStopSwitch&limitStopSwitch=0/1` | GET | Toggle limit-switch safety |
| `/system?action=setTiltStopSwitch&tiltStopSwitch=0/1` | GET | Toggle tilt-sensor safety |
| `/system?action=setMovingStopSwitch&movingStopSwitch=0/1` | GET | Toggle motion-sensor safety |
| `/system?action=setTiltCheckThreshold&tiltCheckThreshold=N` | GET | Tilt threshold (0–255, default 15) |
| `/system?action=setMovingCheckThreshold&movingCheckThreshold=N` | GET | Movement threshold (default 40) |
| `/system?action=setFlameAlarmMode&flameAlarmMode=N` | GET | Flame algorithm |
| `/system?action=setFlameAlarmSensitivity&flameAlarmSensitivity=1/2/3` | GET | High / Low / Off |
| `/cmd?cmd=<gcode>` | GET | Single G-code |
| `/cmd` | POST plain text | Multi-line G-code |
| `/cnc/data?action=pause/resume/stop` | GET | Job control |
| `/list?dir=…` / `/delete?file=…` | GET | SD card files |
| `/upload` or `/cnc/data` | POST multipart | Upload G-code |
| `/upgrade` | POST multipart | Firmware upload (used by Update entity) |
| `/updater` | GET | Web UI for firmware uploads |

`/peripherystatus` JSON shape:

```json
{
  "result": "ok",
  "status": "normal",
  "sdCard": 1,
  "limitStopFlag": 1,
  "tiltStopFlag": 1,
  "movingStopFlag": 1,
  "tiltThreshold": 15,
  "movingThreshold": 40,
  "flameAlarmMode": 3,
  "flameAlarmSensitivity": 1
}
```

D-series flame sensitivity values are `1=high`, `2=low`, `3=off` —
inverse of the S1 mapping (`0/1/2`). The integration converts them in
`DSERIES_FLAME_SENSITIVITY_MAP` (see `const.py`).

### Status-event WebSocket on port 8081

Plain `ws://<ip>:8081/`. The device pushes single-line text frames
whenever its state changes. There is **no command channel** — everything
the integration writes goes via HTTP.

| Frame | Mapped status |
|---|---|
| `ok:IDLE` | `idle` |
| `ok:WORKING_ONLINE` | `processing` |
| `ok:WORKING_ONLINE_READY` | `processing_ready` |
| `ok:WORKING_OFFLINE` | `working_button` |
| `ok:WORKING_FRAMING` | `framing` |
| `ok:WORKING_FRAME_READY` | `frame_ready` |
| `ok:PAUSING` | `paused` |
| `WORK_STOPPED` | `cancelling` |
| `ok:ERROR` | `error_limit` |
| `err:flameCheck` | `error_fire_warning` |
| `err:tiltCheck` | `error_tilt` |
| `err:movingCheck` | `error_moving` |
| `err:limitCheck` | `error_limit` |

The WebSocket listener is started lazily on first poll and runs until
the protocol is disconnected. WS errors are absorbed silently — the
HTTP poll fallback always provides at least the basic working state.

---

## F1 V2 protocol (F1 firmware 40.51+)

Implemented in `f1v2_protocol.py`. **Listener-only** — no documented
command channel; the integration consumes push events for status,
lid, machine lock, and task ID/time.

### Connection

```
wss://<ip>:28900/websocket?id=<random_uuid>&function=instruction
```

- TLS, certificate verification disabled (self-signed device cert).
- Immediately after connect, send TEXT frame
  `bWFrZWJsb2NrLXh0b29s` (handshake token, base64 of `"makeblock-xtool"`).
- Heartbeat: send BINARY `\xC0\x00` every 2 s.
- The probe in `validate_connection` waits up to 5 s for any frame
  after the handshake — if one arrives, the device is F1 V2.

### Frame parsing

- TEXT: raw JSON.
- BINARY: device prefixes a small framing header before the JSON.
  Strategy: locate the first `{`, take everything from there. If the
  payload starts with `{{` (firmware bug observed in BassXT), drop the
  leading byte before parsing.

Frame schema:

```json
{
  "url": "<path>",
  "data": {"module": "...", "type": "...", "info": <varies>},
  "timestamp": 1700000000000
}
```

### Event → state mapping

| `url` | `module` | `type` | Mapped state |
|---|---|---|---|
| `/work/mode` | `STATUS_CONTROLLER` | `MODE_CHANGE` | `info.mode`: `P_SLEEP→sleeping`, `P_WORK/P_READY/P_ONLINE_READY_WORK/P_OFFLINE_READY_WORK→idle/processing_ready`, `P_WORKING→processing`, `P_WORK_DONE/P_FINISH→finished`, `P_ERROR→error_limit` |
| `/device/status` | `STATUS_CONTROLLER` | `WORK_PREPARED` | `framing` if `info=="framing"` else `processing_ready` |
| `/device/status` | `STATUS_CONTROLLER` | `WORK_STARTED` | `framing` or `processing` |
| `/device/status` | `STATUS_CONTROLLER` | `WORK_FINISHED` | `idle` (if framing finished) or `finished` |
| `/work/result` | `WORK_RESULT` | `WORK_FINISHED` | `finished`, captures `info.timeUse`, `info.taskId` |
| `/gap/status` | `GAP` | `OPEN`/`CLOSE` | `state.lid_open` |
| `/machine_lock/status` | `MACHINE_LOCK` | `OPEN`/`CLOSE` | `state.machine_lock` (OPEN=unlocked, CLOSE=locked) |

---

## REST API (F1 / F1 Ultra / F1 Lite / M1 / M1 Ultra / P1 / P2 / P2S)

JSON over HTTP, implemented in `rest_protocol.py`. Verified against the
per-model `index.js` bundles in the XCS APK (`assets/exts/<model>/index.js`).

REST models actually use **three different ports**:

| Port | Purpose |
|---|---|
| 8080 | Main HTTP API — device info, running status, peripherals |
| 8087 | Firmware upload (`/upgrade_version` handshake + `/package` flash) |
| 8329 | Camera (`/camera/snap`, `/camera/exposure`) |

### Main API on port 8080

| Endpoint | Method | Purpose |
|---|---|---|
| `/device/machineInfo` | GET | Serial, model, name, firmware |
| `/device/runningStatus` | GET | Job state, mode |
| `/cnc/status` | GET | Status code mappable to M222 codes |
| `/cnc/data?action=upload&zip=false&id=-1` | POST | Upload G-code |
| `/processing/upload` | POST multipart | Upload processing G-code (P2 family) |
| `/peripheral/fill_light` | POST `{action:"set_bri",idx,value}` | Fill light brightness |
| `/peripheral/laser_head` | POST | `{action:"go_to",x,y,waitTime}` move head, `{action:"get_coord"}` query |
| `/peripheral/ir_led` | POST `{action:"on/off",index}` | IR LED (1=close-up, 2=global) — P2/P2S |
| `/peripheral/gap` | GET | Cover state — `data.state==="off"` means cover open |
| `/peripheral/airassist?action=get` | GET | Air-Assist V2 connect state — `state==="on"` means accessory attached. Used by M1 Ultra. |
| `/config/get` (`type:"user", kv:["airassistCut","airassistGrave"]`) | POST | M1 Ultra default Air-Assist gear for cut and engrave operations. |
| `/config/set` (`type:"user", kv:{airassistCut: <gear>}` or `airassistGrave`) | POST | Set the default Air-Assist gear (applied to next job). |
| `/peripheral/digital_lock` | POST | Lock cover |
| `/peripheral/ir_measure_distance` | POST `{action:"get_distance",type:"single"}` | IR distance |
| `/device/modeSwitch` | POST | Switch mode |
| `/parts` | POST multipart, port **8080** | Upload accessory firmware |
| `/partsProgress` | GET, port **8080** | Accessory firmware update progress |
| `/file?action=…` | GET | Download device files (calibration, machinetype.txt, …) |

### Firmware update on port 8087

Different from the S1's `/burn` flow. Two-step:

| Endpoint | Method | Notes |
|---|---|---|
| `/upgrade_version?force_upgrade=1[&machine_type=<code>]` | GET (or POST) | Handshake. Some models require a `machine_type` param: P2/P2S = `MXP`, M1 Ultra = `MLM`. |
| `/package?action=burn` | POST multipart `file=<bin>` | Upload + flash; device reboots on success |
| `/script` | POST multipart, port **8087** | Upload firmware script (M1) |
| `/burn?reboot=true` | POST, port **8087** | Direct burn (M1) |

The integration's `_flash_rest` in `update.py` follows this flow.

### Camera on port 8329

P2/P2S/F1/F1 Ultra:

| Endpoint | Method | Notes |
|---|---|---|
| `/camera/snap?stream=0` | GET, blob | Global / overview camera |
| `/camera/snap?stream=1` | GET, blob | Local / close-up camera |
| `/camera/exposure?stream=0/1` | POST `{value:<int>}` | Set exposure |
| `/camera/fireRecord` | POST, blob | Recorded flame snapshot (F1 Ultra) |

### REST status mapping

The REST `/cnc/status` JSON uses different codes than M222. The integration
maps both back to the same `XtoolStatus` enum so all model families share
status sensor logic.

---

## Firmware update protocol (cloud)

Update checks hit the public xTool cloud API. **No device authentication is
required** — only the device's serial number is sent.

### Multi-package check (S1)

```
POST https://api.xtool.com/efficacy/v1/packages/version/latest
Content-Type: application/json
```

```json
{
  "domain": "xcs",
  "region": "en",
  "contentId": "xcs-d2-firmware",
  "deviceId": "<serial>",
  "packages": [
    {"contentId": "xcs-d2-0x20", "contentVersion": "<dot-version>"},
    {"contentId": "xcs-d2-0x21", "contentVersion": "<dot-version>"},
    {"contentId": "xcs-d2-0x22", "contentVersion": "<dot-version>"}
  ]
}
```

The `contentVersion` must be parsed from the device's version string into
dot-separated digits with leading zeros stripped, keeping the first three
groups + the last group:

`V40.32.015.2025.01` → `40.32.15.1`

Response (only the boards with available updates):

```json
[
  {
    "id": "xcs-d2-0x20",
    "version": "40.32.015.10",
    "advice": 0|1,
    "title": {"en": "...", "zh": "..."},
    "description": {"en": "...", "zh": "..."},
    "contents": [{"name": "...", "url": "https://...", "md5": "...", "fileSize": 434892}]
  },
  ...
]
```

S1 board IDs and their `burnType` values for the `/burn` endpoint:

| Board ID | Description | `burnType` |
|---|---|---|
| `xcs-d2-0x20` | Main MCU (GD32) | `1` |
| `xcs-d2-0x21` | Laser controller | `2` |
| `xcs-d2-0x22` | ESP32-S3 (Wi-Fi/comm) | `3` |

### Single-package check (REST models)

```
POST https://api.xtool.com/efficacy/v1/package/version/latest
```

```json
{
  "domain": "xcs",
  "region": "en",
  "contentId": "<model_content_id>",
  "deviceId": "<serial>",
  "contentVersion": "<dot-version>"
}
```

Response: a single object with the same shape as one entry of the
multi-package response (or an empty body if no update).

Model content IDs (from APK `extensionData`):

| Model | `contentId` |
|---|---|
| S1 | `xcs-d2-firmware` (multi-package) |
| P2 | `xcs-ext-p2` |
| P2S | `xcs-ext-p2s` |
| F1 | `xcs-ext-f1` |
| F1 Ultra | `xcs-ext-f1-ultra` |
| F1 Lite (GS005) | `xcs-ext-gs005` |
| M1 | `xcs-ext-m1` |
| M1 Ultra | `xcs-ext-m1-lite` |
| D1 | `xcs-ext-d1` |
| D1 Pro | `xcs-ext-d1-pro` |
| D1 Pro 2.0 | `xcs-ext-d1-pro2` |
| P1 | `xcs-ext-p1` |

### Flash flow

1. **S1** — for each board returned by the API:
   - Download the `.bin` from `contents[].url`
   - Send `M22 S3` over WS (enter upgrade mode)
   - `POST /burn` multipart with fields `file=<bin>` and `burnType=<n>`
   - Poll `GET /system?action=get_upgrade_progress` for live `curr_progress / total_progress`
   - Device reboots on success

2. **REST models**:
   - Download the `.bin`
   - Try `POST /upgrade` (single-blob multipart) first
   - On failure fall back to `POST /upgrade_version` (handshake) + `POST /package` (binary)

The install action is **disabled by default**; users must opt in via the
integration options dialog and confirm a risk-warning menu before the
Update entity exposes the install button. See `update.py` and
`config_flow.py`.

---

## Data parsing

### M2003 — full device info

Body is JSON keyed by M-code numbers (`__init__.py` style):

```json
M2003{
  "M310": "MXDK0DD3...",
  "M100": "xTool S1",
  "M116": "X0Y20B1P1L3",
  "M99":  "V40.32.013.2224.01",
  "M1199": "V40.208.003.3D28.01 B1",
  "M2099": "V40.32.013.2224.01 B1",
  "M1098": ["", "", "V40.208.003.3D28.01 B1", "", ...]
}
```

The integration parses this into a `DeviceInfo` dataclass in `protocol.py`.

### M116 — laser module info

`X{type}Y{watts}B{producer}P{process_type}L{laser_tube}` — for example
`X0Y20B1P1L3` = type 0 (Diode), 20 W, producer 1, process type 1, laser
tube 3.

`type` and `power_watts` together determine the human-readable description
(e.g. `"20W Diode"`, `"2W Infrared"`). See `LASER_TYPE_NAMES` and
`LASER_POWERS_IR` in `const.py`.

### M2008 — lifetime statistics

Two formats observed in firmware:

```
M2008 A<work_s> B<jobs> C<standby_s> D<runtime_s>
M2008 A<curr>:<total> B<curr>:<total> C<curr>:<total> D<curr>:<total>
```

The simple format is what the integration parses. The paired format
appears in firmware strings but no command argument has been found that
emits it.

### M1098 — accessories

Comma-separated quoted strings. Each position represents a fixed accessory
slot:

| Index | Accessory |
|---|---|
| 0 | Purifier |
| 1 | Fire extinguisher |
| 2 | Air pump 1.0 |
| 3 | Air pump 2.0 |
| 4 | Fire extinguisher v1.5 |

Non-empty values are firmware version strings (`Vx.y.z`) for that
accessory; empty string means absent.

---

## Stored config (S1 NVS)

The S1's main MCU stores a large JSON config blob (`S1_CONFIG`) in NVS.
Discovered fields include:

- Per-laser-power lifetime: `acc_2w_laserworktime`, `acc_10w_laserworktime`,
  `acc_20w_laserworktime`, `acc_40w_laserworktime`,
  `acc_default_laserworktime`, `acc_sys_runtime`, `acc_workcount`
- Temperature thresholds (write-time only): `laser_2w_preheat_temp`,
  `laser_2w_over_heat_temp`, `laser_default_preheat_temp`,
  `laser_default_over_heat_temp`, `laser_temp_report` (boolean toggle)
- Motion calibration: `motion_x_soft_limit`, `motion_y_soft_limit`,
  `motion_z_soft_limit`, `home_x/y/z_distance`, `bl_*` (BLTouch),
  `motion_micro_step`, `hold_current`, `run_current`
- Misc: `fan_off_delaytime`, `fill_light_brightness`,
  `mc3416_threshold` (G-sensor), `print_prepare_time`

No M-code or HTTP endpoint to read this dump has been found. The values
appear to be write-only via individual M-codes. **Live laser temperature
in particular is logged internally (`%dw laser temp less than %.3f`) but
not exposed.**

---

## Hardware features hinted at in firmware (not yet implemented)

- **Cover/lid sensor** — `plugin_cover.c` in main MCU, "cover open" cancels
  the running job. Probably reachable as a push frame; M-code unknown.
- **G-sensor / accelerometer** — mc3416 / da215s for tilt/movement detect.
- **Two-stage flame detection** — firmware logs `fire first happened alarm`
  (warning) before `fire second happened and fire box work` (full alarm).
  Could expose as a separate `error_fire_warning` status (enum value
  reserved, but the M222 S-code that emits it is not yet confirmed).

---

## References

- `discovery.py` — UDP scan
- `protocol.py` — abstract base, dataclasses, parse helpers
- `ws_protocol.py` — S1 WebSocket implementation, XCS compat mode
- `http_mcode_protocol.py` — D-series HTTP M-code
- `rest_protocol.py` — F1/P2/M1/P1/GS REST API
- `firmware.py` — cloud update API client
- `update.py` — `UpdateEntity` integration with progress polling
- `const.py` — every M-code, HTTP path, action name as a constant. **No
  string literal of an M-code or path may live outside this file.**
