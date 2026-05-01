# xTool Device Protocols

Network protocol reference for every xTool laser, engraver, fiber-laser
welder and inkjet printer covered here. Two primary sources:

- **xTool Studio** Windows app (`resources/exts.zip/<model>/index.js`,
  v3.70.90 at the time of writing) — current, ships per-model JS bundles
  for all 20 known devices including the recent P3, F2 family, MetalFab
  (HJ003), F1 Ultra V2 (GS003), Apparel Printer (DT001). The auto-extracted
  api tables in each family section come straight from these bundles.
  Cloud firmware IDs use the `atomm` domain with `xTool-*` prefixes (see
  [Cloud content IDs](#cloud-content-ids-and-machine_type-per-model)).
- **Live probes against an xTool S1** — the only hardware on hand. Used
  to confirm command/response shapes (e.g. M1109/M1113/M2240 replies)
  and to validate flash flows.

Older xTool **XCS Android** APK bundles (`assets/exts/<model>/index.js`)
were the earliest source and remain useful for cross-checking the
legacy `xcs-*-firmware` cloud namespace; they're no longer the primary
reference. Cross-checks against community projects
([Doormat1/XTool_D1_HA](https://github.com/Doormat1/XTool_D1_HA),
[BassXT/xtool](https://github.com/BassXT/xtool),
[1RandomDev/xTool-Connect](https://github.com/1RandomDev/xTool-Connect/blob/master/XTOOL_PROTOCOL.md))
filled in the few bits encrypted in the XCS APK by Pairip.

## Protocol families

Four transport flavours. Studio's own naming uses `S1` / `V1` / `V2`
(per `protocolName` field in the `connectWithRetry` factory) plus the
D-series HTTP-write + WS-status-push hybrid that Studio bundles with
V1 via different `connectConfigs` flags (`channelType: "serial"` on
USB, `needConnectAlive: false`, no heartbeat).

| Family | Studio name | Models | Transport | Port(s) |
|---|---|---|---|---|
| `s1` | `S1` | S1 | bidirectional WebSocket G-code RPC + HTTP fallback | 8081 (WS), 8080 (HTTP) |
| `d_series` | `V1` (variant) | D1, D1 Pro, D1 Pro 2.0 | HTTP write + read-only status-push WebSocket | 8080 (HTTP), 8081 (WS) |
| `rest` | `V1` | F1, F1 Ultra, F1 Ultra V2 (GS003), F1 Lite (GS005), F2 (GS006), F2 Ultra (GS004-CLASS-4), F2 Ultra Single (GS007-CLASS-4), F2 Ultra UV (GS009-CLASS-4), M1, M1 Ultra, MetalFab (HJ003), P1, P2, P2S, P3, Apparel Printer (DT001) — V1-firmware path | HTTP REST (JSON) | 8080 (main), 8087 (firmware), 8329 (camera) |
| `ws_v2` | `V2` | V2-firmware line — see [WS-V2 firmware activation thresholds](#ws-v2-firmware-activation-thresholds) below | TLS WebSocket request/response API + push events; three concurrent channels (`function=instruction` / `file_stream` / `media_stream`) | 28900 (wss) |

V1- and V2-firmware lines for the same hardware coexist — discovery +
the port-28900 probe pick the right family at config-flow time. See
[WS-V2 protocol](#ws-v2-protocol-tls-websocket-rpc--push) below for the
full V2 wire contract.

All four are **local-only**. The cloud is only contacted for firmware
update checks and firmware-image downloads.

### WS-V2 firmware activation thresholds

The V2 communication framework rolled out per-model on different
firmware versions. Devices on or above the listed version answer the
encrypted multicast discovery + WS-V2 request/response API on port
28900; devices below stay on the legacy REST family on port 8080.
Versions sourced from the cloud's "Communication Framework Upgrade"
release notes published on `api.xtool.com/efficacy/v1/package/version/latest`.

| Model | Min V2 firmware | Notes |
|---|---|---|
| F1 | `40.51.020.04` | Communication framework upgrade. Breaks LightBurn + XCS Mobile. |
| F1 Ultra | `40.52.016.05` | Communication framework upgrade. Breaks LightBurn + XCS Mobile. |
| F1 Ultra V2 (GS003) | `40.53.007.05` | Communication framework upgrade. Breaks LightBurn + XCS Mobile. |
| F1 Lite (GS005) | `40.55.020.04` | Communication framework upgrade. Breaks LightBurn + XCS Mobile. |
| F2 (GS006) | `40.56.021.08` | Numbering aligned with the V2 family; release notes mention only flame-detection + bug-fixes. |
| F2 Ultra (GS004-CLASS-4) | `40.54.020.05` | Core system framework + protocol upgrade. Studio v1.4+ required. |
| F2 Ultra Single (GS007-CLASS-4) | `40.57.020.05` | Core system framework + protocol upgrade. Studio v1.4+ required. |
| F2 Ultra UV (GS009-CLASS-4) | `40.130.021.02` | Numbering aligned with the V2 family. |
| M1 Ultra | `40.41.017` | Communication framework upgrade. Breaks XCS Mobile. |
| P2S | `40.22.011.06` | Communication framework upgrade. Breaks LightBurn + XCS Mobile. |
| P3 | `40.23.006.03` | Ships V2-only. ⚠️ Update can take 10–15 min. |
| MetalFab (HJ003) | `40.70.013.4` | Studio v1.6+ required. |
| Apparel Printer (DT001) | `40.100.025.03` | Includes manual ink-stack calibration + alignment-reset features. |

V1-firmware lines that have **not** moved to V2 yet: D1 / D1 Pro /
D1 Pro 2.0 (D-series stays on legacy REST + push-WS), M1, P1, P2, S1
(S1 has its own `ws_mcode` family unrelated to V1/V2).


## Discovery

xTool Studio runs **two** discovery flows in parallel — V1 (legacy
plain UDP) and V2 (encrypted multicast). Source of truth:
`xTool Studio/resources/app.asar` →
`.vite/build/discover-worker.d0392b78.cjs`. Two classes coexist:
`LegacyMulticastServer` (V1) and `MulticastServer` (V2). Either may
fire first, so a robust client mirrors both and dedupes by IP.

### Discovery V1 (legacy plain UDP, port 20000)

V1-firmware devices (S1, D-series, plus any F1/M1/P2/F2 still on V1
firmware) listen on UDP/20000 for a JSON probe.

Request (broadcast to `255.255.255.255:20000`):

```json
{"requestId": <random_int>}
```

Reply (unicast back from device):

```json
{"requestId": <echo>, "ip": "192.168.x.x", "name": "xTool S1", "version": "V40.32.013.2224.01"}
```

Studio also multicasts the probe to `224.0.1.77:20000` for V1 — both
target choices reach the same firmware. The HA integration sticks to
the local broadcast since that already covers the LAN scope.

### Discovery V2 (encrypted multicast)

V2-firmware devices (per-model thresholds in
[WS-V2 firmware activation thresholds](#ws-v2-firmware-activation-thresholds)
above) **do not** answer the plain V1 probe. They expect an
AES-256-CBC encrypted `deviceFind` envelope on the multicast network.

#### Targets

Broadcast — send to all four:

```
224.0.0.251:5353     link-local, TTL 1
224.0.0.252:5354     link-local, TTL 1
239.0.1.251:25353    private,    TTL 4
239.0.1.252:25354    private,    TTL 4
```

Unicast (manual IP) — send to both:

```
<targetIP>:25353
<targetIP>:25454       (note: 25454, NOT 25354)
```

#### Socket layout

xTool Studio's `MulticastServer.initReceivers` binds **four RX sockets**
— one per multicast port — each joined to its corresponding group via
`IP_ADD_MEMBERSHIP` and using `SO_REUSEADDR`. A separate **TX socket**
on a random ephemeral port handles outbound sends + receives unicast
replies.

```
RX 0.0.0.0:5353   join 224.0.0.251
RX 0.0.0.0:5354   join 224.0.0.252
RX 0.0.0.0:25353  join 239.0.1.251
RX 0.0.0.0:25354  join 239.0.1.252
TX 0.0.0.0:<rand>           (sends + accepts unicast replies)
```

Without the four RX sockets bound to the well-known ports, the kernel
silently drops multicast replies destined for `5353` etc. — group
membership alone is not enough. The TX socket alone catches only the
unicast leg of a reply.

#### Encryption

- AES-256-CBC, PKCS#7 padding.
- 16-byte random IV prepended to ciphertext (sent over the wire as
  `IV ‖ ciphertext`).
- Static key:

  ```
  commonKey = "makeblocsdbfjssjkkejqbcsdjfbqlla"   // 32 bytes
  ```

#### Request payload (plaintext, encrypted before send)

```json
{
  "type": "deviceFind",
  "method": "request",
  "data": {
    "version":    "1.0",
    "clientType": "atomnClient",
    "requestId":  <uint32 random>,
    "key":        "makeblocsdbfjssjkkejqbcsdjfbqlla"
  }
}
```

#### Response payload (decrypted, same key)

```json
{
  "type":   "deviceFind",
  "method": "response",
  "data": {
    "requestId":       <echo>,
    "ip":              "192.168.x.x",
    "deviceIp":        "<usually same as ip>",
    "deviceName":      "xTool F1",
    "version":         "1.0",
    "deviceCode":      "F1",
    "deviceId":        "<uuid>",
    "deviceSn":        "<serial>",
    "key":             "<per-device key, informational>",
    "netType":         "WIFI",
    "firmwareVersion": "40.51.xxx",
    "platformVersion": "..."
  }
}
```

The device's per-response `key` field is informational — Studio
decrypts everything with the static `commonKey`. The richer field set
(`deviceSn`, `deviceCode`, `firmwareVersion`) lets a client populate
the config entry's `unique_id` straight from discovery.

A second key, `primaryKey = "makeblockmakeblockmakeblock-2025"`, lives
in the same source file. It belongs to the cloud-binding flow, not
discovery — ignore it for LAN device search.

#### Deployment caveats

Common LAN-side reasons V2 discovery fails (HA + similar integrations):

- **Docker without `network_mode: host`** — multicast does not cross
  the bridge to a container. Either run HAOS / supervised, or expose
  the container on the host network.
- **Multi-NIC host** — `INADDR_ANY` joins the multicast group on the
  default route's interface. On a host with both Docker bridge and
  LAN, the join can land on the wrong NIC. Workaround: explicit
  `IP_MULTICAST_IF` per RX socket.
- **Firewalls / managed switches** that block IGMP or drop traffic on
  the V2 multicast ports.
- **Sleep / power state** — V2 firmware may pause the encrypted
  responder while the device is in the deepest sleep tier. Wake the
  device first.

When discovery cannot identify a device, fall back to a manual model
picker: the user supplies the IP and selects the (model,
protocol_version) pair from a registry-driven dropdown, and the
client jumps straight to the per-protocol handshake (port-28900 TLS
WS for V2, REST/8080 for V1, M-code WS/8081 for S1, …). UDP discovery
is a hint, not a hard requirement.


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
- `M15 A{n} S{n}` — air assist + light active

Sending `M2211` triggers the device to push a full state burst (all
M-codes listed above) — useful as a cheap state refresh without a full
`M2003` round-trip.

### XCS Compatibility Mode

The XCS desktop app holds the WebSocket exclusively — when it connects,
the device kicks any other WS client. A typical detection / fallback
strategy:

- ≥ 3 disconnects within 30 s while a session lasted < 10 s ⇒ assume
  XCS has taken over the WS slot.
- While XCS holds the WS, control writes still work via `POST /cmd` over
  HTTP (port 8080) — see the HTTP endpoints section below.
- A recovery probe (e.g. every 60 s) tests whether the WS is free
  again; two clean status queries in a row are a reliable signal.

### M-code reference (S1)

Conventions: `{x}` = integer, `{x.y}` = float, `"…"` = quoted string.
Codes marked **(WS-only)** do not work via HTTP `/cmd`.

#### Queries

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
| `M9098` | `M9098 [...]` JSON-ish list | Bluetooth dongle: connected accessories snapshot (polled every 60 s) |

#### Control

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

#### Z-probe / measure-mode M-codes (not implemented)

The S1 has a motorized Z-probe pin behind the laser carriage. The XCS app
exposes it via a `measure()` flow:

```
M312 S1            # enterMeasureMode
M311 S0            # startMeasure (device pushes M313 X<>Y<>Z<> + M311 S2)
# afterMeasure:
G0 Z-2 F900
G0 X<rx> Y<ry> F12000   # park at xTouchResetPos (M366 reply)
M311 R0            # resetFocusModel
M312 S0            # exitMeasureMode
```

Relevant M-codes are `M311 S0/R0`, `M312 S0/S1`, `M313`, `M366`, `M110`.
On the live S1 the full sequence could not be reliably reproduced over
either the WebSocket or HTTP `/cmd` channel — the measurement either
silently no-ops or partially executes without retracting the pin.

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

#### Codes present in firmware but not on the documented wire path

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

#### S1 M-codes mapped to xTool Studio API names (newly identified)

xTool Studio's `exts/S1/index.js` exposes named axios apis whose `cmd:`
strings reveal the purpose of many otherwise-opaque codes:

| Code | xTool Studio api name | Notes |
|---|---|---|
| `M345 T<n>` | (in M2003 dump) | flag; default `T=1` — usbKey/USB connected? |
| `M362 S<n>` | xTouchConnected | always `S1` on built-in touch panel |
| `M363 S0/S2/S3/S4/S5` | multi-point measurement state | `S0`=reset, `S2`=success, `S4`=failure |
| `M366 X<f> Y<f>` | xTouchResetPos | parked-pin coordinates for Z-probe (see Z-probe section) |
| `M372` | handleReceiveMultiPoint | multi-point measurement result push |
| `M1109` | getRedCrossInfo | Red-cross laser pointer calibration. Live S1 reply: `M1109 A<bottomX> B<bottomY> C<topX> D<topY> E<maxZ>` (e.g. `A-0.399 B21.641 C0.241 D20.281 E58.000`). X/Y offsets at Z=0 and Z=`maxZ` (workspace height in mm); used to compensate parallax of the red-cross pointer. |
| `M1113` | getZOffset (xtouchOffsetStr) | xTouch (Z-probe pin) X/Y offset from the laser nozzle. Live S1 reply: `M1113 X<x> Y<y>` (e.g. `X21.761 Y21.479`). The pin sits offset from the laser axis; this value lets the host compensate position when reporting/storing the measured Z. |
| `M2240` | lightInterference | Ambient-light interference detector configuration. Live S1 reply: `M2240 A<f> B<f> C<n> D<n> M<f> P<f> I<n>` (e.g. `A0.500000 B0.800000 C50 D50 M300.0 P0.6 I0`). Read-only PID-style coefficients used by the flame-alarm subsystem to discriminate real flame from ambient light flicker. |
| `M322` | canWriteData | gate for file uploads (`R0`/`R1` reply) |
| `M325 S<n>` | setFileTransferStatus | multi-block upload control |
| `M328` | cancelWriteFile | abort in-flight upload |
| `M329` | exportLog | trigger device-side log export |
| `M2503` | (literal constant) | placeholder/test |
| `M807 N<n>` | enterBoot / loginOutBoot | bootloader entry/exit |
| `M9032` | getPurifierV3RCVersion | **V3** AP2 purifier remote-controller version (newer than V2) |
| `M9033` | getPurifierInfo | V3 purifier full status (replaces M9039 V2 push) |
| `M9039` | getPurifierState | **V2** AP2 purifier |
| `M9043` | (V3 purifier reserved) | unknown |
| `M9046 S<n>` | setPurifierV3Buzzer | toggle V3 purifier buzzer |
| `M9055 W<n> A<n> B<n> C<n>` | filter usage report | which / total / used (V3) |
| `M9064 A<n> B<n> S<n>` | setFanGear / setFanGearV3 | duct-fan gear |
| `M9066` | updateOptimizeFan | (re)trigger fan optimisation |
| `M9079 S<n>` | setFanBuzzer | duct-fan buzzer |
| `M9081` | setDuctMotorStallDebug | debug stall detection |
| `M9082` | getFanInfo | duct-fan diagnostic snapshot |
| `M9085 T<seconds>` | setFanV3RunDuration | V3 fan post-run timer |
| `M9091 E0/E1` | BLE scan | dongle |
| `M9092 T<ms>` | BLE list nearby | dongle (already documented) |
| `M9093 A<MAC> B<n>` | BLE pair | dongle |
| `M9097 A<MAC>` | BLE probe | dongle |
| `M9098` | BLE connected snapshot | dongle (currently used) |
| `M9112` | setBluetoothUnbind | dongle: forget paired device |
| `M9258` | resetFilterWorkTime | reset purifier filter timer |

Many V3-purifier and duct-fan codes are sent over the **BLE dongle**
sub-protocol (`uart485`/F0F7 framing, not raw WS) — see the Bluetooth
dongle section.

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
### Data parsing (S1)

#### M2003 — full device info

Body is JSON keyed by M-code numbers:

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

#### M116 — laser module info

`X{type}Y{watts}B{producer}P{process_type}L{laser_tube}` — for example
`X0Y20B1P1L3` = type 0 (Diode), 20 W, producer 1, process type 1, laser
tube 3. `type` and `power_watts` together produce a human-readable
description (e.g. `"20W Diode"`, `"2W Infrared"`).

#### M2008 — lifetime statistics

Two formats observed in firmware:

```
M2008 A<work_s> B<jobs> C<standby_s> D<runtime_s>
M2008 A<curr>:<total> B<curr>:<total> C<curr>:<total> D<curr>:<total>
```

The simple form is what the device emits in response to a bare
`M2008 A1` query. The paired form appears in firmware strings but the
exact command argument that emits it has not been confirmed.

#### M1098 — accessories

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

### Stored config (S1 NVS)

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

### Hardware features hinted at in firmware (not yet implemented)

- **Cover/lid sensor** — `plugin_cover.c` in main MCU, "cover open" cancels
  the running job. Probably reachable as a push frame; M-code unknown.
- **G-sensor / accelerometer** — mc3416 / da215s for tilt/movement detect.
- **Two-stage flame detection** — firmware logs `fire first happened alarm`
  (warning) before `fire second happened and fire box work` (full alarm).
  Could expose as a separate `error_fire_warning` status (enum value
  reserved, but the M222 S-code that emits it is not yet confirmed).

### Full xTool Studio S1 api inventory

Auto-extracted from `xTool Studio v3.70.90 / exts/S1/index.js`. Every axios api block that carries an `url:` or `cmd:` is listed. `?` in the description column means the api name didn't match the curated lookup — purpose is unknown but the endpoint/M-code is real.

| api | method | path / cmd | description |
|---|---|---|---|
| `afterWriteFile` | POST | `M323 ${s (fn)` | Per-block write ack (`M3 S0/S1`). |
| `airAssistCloseDelay` | SET | `M1099` | Air-assist post-run timer (`M1099 T<seconds>`). |
| `airAssistV2` | POST | /peripheral/airassistV2 (port 8080) | AirAssist V2 BLE control (pairing / mode switch via /peripheral/airassistV2 + M9091 family). |
| `airAssistV2` | GET | `M499 S0 T1` | AirAssist V2 BLE control (pairing / mode switch via /peripheral/airassistV2 + M9091 family). |
| `airAssistV2` | PUT | /v1/peripheral/param | AirAssist V2 BLE control (pairing / mode switch via /peripheral/airassistV2 + M9091 family). _params: `{type:"airassistV2"}`_ |
| `allFirmwareVersion` | GET | `M2003` | Per-board firmware version JSON (`M2003`). |
| `beeperEnable` | SET | `M21` | ? |
| `cancelPrint` | GET | `M108` | Cancel job (REST). |
| `cancelWriteFile` | GET | `M328` | Abort upload (`M328`). |
| `canWriteData` | GET | `M322` | Pre-upload gate (`M322` → `R0`/`R1`). |
| `checkDeviceBootStatus` | GET | `checkBoot` | ? |
| `checkXTouchStatu` | GET | `M362` | ? |
| `configWifi` | SET | `M2001 (fn)` | Set Wi-Fi credentials (`M2001 "<ssid>" "<pwd>"`). |
| `ContentNode` | — | Fv(y.url) | ? |
| `curveMeasurement` | — | c.ext.commonResource.sCurveMeasureImg | ? |
| `cylinder` | — | eTe(r | ? |
| `deviceInfo` | GET | `M2003` | Full device dump (collects M2003 + many follow-ups). |
| `enableOddEvenKerfGroup` | — | vke | ? |
| `enterBoot` | SET | `M807 N0` | ? |
| `enterMeasureMode` | POST | `M312 S1` | Enter Z-probe measurement mode (`M312 S1`). |
| `enterMultiPointMode` | SET | `M22 S4` | Enter multi-point measurement (`M22 S4`). |
| `enterUpgradeMode` | SET | `M22 S3` | Enter firmware-upgrade mode (`M22 S3`). |
| `exitMeasureMode` | POST | `M312 S0` | Leave measurement mode (`M312 S0`). |
| `exitMultiPointMode` | SET | `M108` | Leave multi-point mode (`M108`). |
| `exitUpgradeMode` | SET | `M108` | Leave firmware-upgrade mode (M108). |
| `exportLog` | GET | `M329` | Trigger device-side log export (`M329`). |
| `exportLog` | — | /gcode/logs.txt | Trigger device-side log export (`M329`). |
| `fireLevel` | GET | `M343` | Read fire-detection level (`M343 S<n>`). |
| `flameAlarm` | SET | `M340 S1` | Flame alarm sensitivity (`M340`). |
| `focalLength` | — | c.ext.commonResource.focalLengthImg | ? |
| `getAccessoriesListViaV2Platform` | GET | /v1/platform/accessories/list | ? _params: `{}`_ |
| `getAirflow` | GET | `M9009` | ? |
| `getAllDangleConnectList` | POST | `M9098` → /passthrough | ? |
| `getAllDangleConnectList` | GET | `M9098` | ? |
| `getAllDangleConnectList` | POST | `M9098` → /v1/parts/control | ? |
| `getAllDangleConnectList` | POST | /v1/platform/accessories/control | ? _params: `{id:i_}`_ |
| `getAllDangleList` | POST | `M9092 T5000` → /passthrough | List nearby BLE accessories (`M9092 T<ms>`). |
| `getAllDangleList` | GET | `M9092 T5000` | List nearby BLE accessories (`M9092 T<ms>`). |
| `getAllDangleList` | POST | `M9092 T5000` → /v1/parts/control | List nearby BLE accessories (`M9092 T<ms>`). |
| `getAllDangleList` | POST | /v1/platform/accessories/control | List nearby BLE accessories (`M9092 T<ms>`). _params: `{id:i_}`_ |
| `getBackpackPurifierInfo` | POST | `M9033` → /passthrough | ? |
| `getBackpackPurifierInfo` | POST | `M9033` → /v1/parts/control | ? |
| `getDangleVersion` | POST | `M99` → /passthrough | ? |
| `getDangleVersion` | GET | `M2003` | ? |
| `getDangleVersion` | POST | `M99` → /v1/parts/control | ? |
| `getDangleVersion` | POST | /v1/platform/accessories/control | ? _params: `{id:i_}`_ |
| `getDeviceStatus` | GET | `M222` | Query work-state (`M222`). |
| `getDeviceStatus_v2` | GET | `M222` | ? |
| `getDongleConnectStatus` | POST | /device/machineInfo (port 8080) | ? |
| `getDongleConnectStatus` | GET | `M2003` | ? |
| `getDongleConnectStatus` | GET | /v1/device/machineInfo | ? |
| `getDongleConnectStatus` | POST | /v1/platform/accessories/control | ? _params: `{id:i_}`_ |
| `getFanBootVersion` | POST | `M99` → /passthrough | ? |
| `getFanBootVersion` | GET | `boot` | ? |
| `getFanBootVersion` | POST | `M99` → /v1/parts/control | ? |
| `getFanBootVersion` | POST | /v1/platform/accessories/control | ? _params: `{id:ih}`_ |
| `getFanInfo` | POST | `M9082` → /passthrough | Duct-fan diagnostic snapshot (`M9082`). |
| `getFanInfo` | GET | `M9082` | Duct-fan diagnostic snapshot (`M9082`). |
| `getFanInfo` | POST | `M9082` → /v1/parts/control | Duct-fan diagnostic snapshot (`M9082`). |
| `getFanInfo` | POST | /v1/platform/accessories/control | Duct-fan diagnostic snapshot (`M9082`). _params: `{id:ih}`_ |
| `getFanInfoV3` | POST | `M9082` → /passthrough | ? |
| `getFanInfoV3` | GET | `M9082` | ? |
| `getFanInfoV3` | POST | `M9082` → /v1/parts/control | ? |
| `getFanInfoV3` | POST | /v1/platform/accessories/control | ? _params: `{id:oc}`_ |
| `getFanV3BootVersion` | POST | `(fn)` → /passthrough | ? |
| `getFanV3BootVersion` | GET | `boot` | ? |
| `getFanV3BootVersion` | POST | `(fn)` → /v1/parts/control | ? |
| `getFanVersion` | GET | ` ` | ? |
| `getFanVersion` | POST | /v1/platform/accessories/control | ? _params: `{id:ih}`_ |
| `getFanVersionV3` | POST | `M99` → /passthrough | ? |
| `getFanVersionV3` | GET | `M99` | ? |
| `getFanVersionV3` | POST | `M99` → /v1/parts/control | ? |
| `getFanVersionV3` | POST | /v1/platform/accessories/control | ? _params: `{id:oc}`_ |
| `getFlameAlarm` | GET | `M340` | Read flame alarm setting (`M340`). |
| `getHandheldGear1Power` | POST | /v1/project/device/accessory/control | ? _params: `{level:1}`_ |
| `getHandheldGear2Power` | POST | /v1/project/device/accessory/control | ? _params: `{level:2}`_ |
| `getLaserCoord` | GET | `M303` | ? |
| `getMachiningPower` | POST | /v1/project/device/accessory/control | ? _params: `{}`_ |
| `getMultiFunctionalBaseInfo` | POST | /v1/platform/accessories/control | ? _params: `{id:T1}`_ |
| `getNonBleAccessories` | POST | /device/machineInfo (port 8080) | ? |
| `getNonBleAccessories` | GET | `M2003` | ? |
| `getNonBleAccessories` | GET | /v1/device/machineInfo | ? |
| `getNonBleAccessoryFirmwareInfo` | POST | /device/machineInfo (port 8080) | ? |
| `getNonBleAccessoryFirmwareInfo` | GET | `M2003` | ? |
| `getNonBleAccessoryFirmwareInfo` | GET | /v1/device/machineInfo | ? |
| `getPurifierBootVersion` | POST | `M99` → /passthrough | ? |
| `getPurifierBootVersion` | GET | `boot` | ? |
| `getPurifierBootVersion` | POST | `M99` → /v1/parts/control | ? |
| `getPurifierBootVersion` | POST | /v1/platform/accessories/control | ? _params: `{id:Sy}`_ |
| `getPurifierInfo` | POST | `M9033` → /passthrough | Purifier V3 status snapshot (`M9033`, BLE F0F7 framing). |
| `getPurifierInfo` | GET | `M9033` | Purifier V3 status snapshot (`M9033`, BLE F0F7 framing). |
| `getPurifierInfo` | POST | `M9033` → /v1/parts/control | Purifier V3 status snapshot (`M9033`, BLE F0F7 framing). |
| `getPurifierInfo` | POST | /v1/platform/accessories/control | Purifier V3 status snapshot (`M9033`, BLE F0F7 framing). _params: `{id:Sy}`_ |
| `getPurifierInfoV3` | POST | `M9033` → /passthrough | Purifier V3 status (`M9033`). |
| `getPurifierInfoV3` | GET | `M9033` | Purifier V3 status (`M9033`). |
| `getPurifierInfoV3` | POST | `M9033` → /v1/parts/control | Purifier V3 status (`M9033`). |
| `getPurifierInfoV3` | POST | /v1/platform/accessories/control | Purifier V3 status (`M9033`). _params: `{id:yd}`_ |
| `getPurifierV3BootVersion` | POST | `M99` → /passthrough | ? |
| `getPurifierV3BootVersion` | GET | `M99` | ? |
| `getPurifierV3BootVersion` | POST | `M99` → /v1/parts/control | ? |
| `getPurifierV3BootVersion` | POST | /v1/platform/accessories/control | ? _params: `{id:yd}`_ |
| `getPurifierV3RCVersion` | POST | `M9032` → /passthrough | Purifier V3 RC firmware version (`M9032`). |
| `getPurifierV3RCVersion` | GET | `M9032` | Purifier V3 RC firmware version (`M9032`). |
| `getPurifierV3RCVersion` | POST | `M9032` → /v1/parts/control | Purifier V3 RC firmware version (`M9032`). |
| `getPurifierV3RCVersion` | POST | /v1/platform/accessories/control | Purifier V3 RC firmware version (`M9032`). _params: `{id:yd}`_ |
| `getPurifierV3Version` | POST | `M99` → /passthrough | ? |
| `getPurifierV3Version` | POST | `M99` → /v1/parts/control | ? |
| `getRedCrossInfo` | GET | `M1109` | Red-cross laser pointer calibration (bottom/top X/Y + maxZ) via `M1109`. |
| `getSafetyFireBoxProInfo` | POST | /v1/platform/accessories/control | ? _params: `{id:lae}`_ |
| `getSdCardStatus` | GET | `M321` | SD card present (`M321`). |
| `getTaskId` | GET | `M810` | ? |
| `getTaskTime` | GET | `M815` | ? |
| `getUltrasonicKnifeControlMode` | POST | /v1/project/device/accessory/control | ? _params: `{}`_ |
| `getZOffset` | GET | `M1113` | Read xTouch (Z-probe) X/Y offset from laser head (`M1113`). |
| `lightInterference` | GET | `M2240` | Ambient light interference sensor reading (`M2240`). |
| `listWifi` | GET | `M2000` | Scan available SSIDs (`M2000` or `/net/get_ap_list`). |
| `listWifi` | — | /net/get_ap_list | Scan available SSIDs (`M2000` or `/net/get_ap_list`). |
| `loginOutBoot` | SET | `M807 N1` | ? |
| `measureDistance` | — | c.deviceValues.mode===Ke.LASER_CYLINDER?c.ext.commonResource.redCrossMeasureGif:c.ext.commonResource.autoMeasureImg | ? |
| `moveGoTo` | SET | `(fn)` | ? |
| `moveLaser` | SET | `(fn)` | ? |
| `moveLaserToZero` | SET | `M111 S7` | Park laser head at the workspace origin. |
| `moveLaserXYToZero` | SET | `M111 S3` | ? |
| `moveLaserZToZero` | SET | `M111 S2` | ? |
| `moveStop` | SET | `M318` | Move-stop safety toggle (`M318`). |
| `moveToPoint` | SET | `(fn)` | Absolute move to (X,Y,Z) at given feedrate. |
| `moveToResetFocusModel` | SET | `(fn)` | Move XY to xTouch reset position (`G0 X<x> Y<y> F<speed>`). |
| `moveZToPoint` | SET | `(fn)` | Move Z to absolute height (`M110 X1 Y1 Z1` + `G90` + `G0 Z<z> F<speed>`). |
| `multiPoint` | — | t.ext.commonResource.multiPointImg | ? |
| `pausePrint` | SET | `M22 S1` | ? |
| `queryAirAssist` | GET | `M15` | ? |
| `querySmokingFan` | GET | `M7` | ? |
| `redCrossOffset` | SET | `M98` | ? |
| `resetFilterWorkTime` | POST | /v1/platform/accessories/control | Reset purifier filter timer (`M9258`). _params: `{id:T1}`_ |
| `resetFilterWorkTime` | POST | `M9258 ${r.data.filterType}0` → /passthrough | Reset purifier filter timer (`M9258`). |
| `resetFilterWorkTime` | POST | `M9258 ${r.filterType}0` → /v1/parts/control | Reset purifier filter timer (`M9258`). |
| `resetFocusModel` | SET | `M311 R0 (fn)` | Retract Z-probe pin (`M311 R0`). |
| `resumeProcessing` | SET | `M22 S0` | ? |
| `riseUp` | GET | `M2003` | ? |
| `setAirflow` | SET | `M9009 S${t} (fn)` | ? |
| `setBluetoothConnect` | POST | `M9093 A${r} B1` → /passthrough | ? |
| `setBluetoothConnect` | GET | `M9093 A${r (fn)` | ? |
| `setBluetoothConnect` | POST | `M9093 A${r} B1` → /v1/parts/control | ? |
| `setBluetoothConnect` | POST | /v1/platform/accessories/control | ? _params: `{id:i_}`_ |
| `setBluetoothScanOff` | POST | `M9091 E0` → /passthrough | BLE scan off (`M9091 E0`). |
| `setBluetoothScanOff` | SET | `M9091 E0` | BLE scan off (`M9091 E0`). |
| `setBluetoothScanOff` | POST | `M9091 E0` → /v1/parts/control | BLE scan off (`M9091 E0`). |
| `setBluetoothScanOff` | POST | /v1/platform/accessories/control | BLE scan off (`M9091 E0`). _params: `{id:i_}`_ |
| `setBluetoothScanOn` | POST | `M9091 E1 D180` → /passthrough | BLE scan on (`M9091 E1`). |
| `setBluetoothScanOn` | SET | `M9091 E1 D180` | BLE scan on (`M9091 E1`). |
| `setBluetoothScanOn` | POST | `M9091 E1 D180` → /v1/parts/control | BLE scan on (`M9091 E1`). |
| `setBluetoothScanOn` | POST | /v1/platform/accessories/control | BLE scan on (`M9091 E1`). _params: `{id:i_}`_ |
| `setBluetoothUnbind` | POST | `M9097 A${r}` → /passthrough | BLE forget paired device (`M9112`). |
| `setBluetoothUnbind` | SET | `M9097 A${r (fn)` | BLE forget paired device (`M9112`). |
| `setBluetoothUnbind` | POST | `M9097 A${r}` → /v1/parts/control | BLE forget paired device (`M9112`). |
| `setBluetoothUnbind` | POST | /v1/platform/accessories/control | BLE forget paired device (`M9112`). _params: `{id:i_}`_ |
| `setDeviceName` | SET | `M100 (fn)` | ? |
| `setDuctMotorStallDebug` | POST | `M9081 A${r}` → /passthrough | Duct-fan stall-detect debug (`M9081`). |
| `setDuctMotorStallDebug` | SET | `M9081 A${r (fn)` | Duct-fan stall-detect debug (`M9081`). |
| `setDuctMotorStallDebug` | POST | `M9081 A${r}` → /v1/parts/control | Duct-fan stall-detect debug (`M9081`). |
| `setDuctMotorStallDebug` | POST | /v1/platform/accessories/control | Duct-fan stall-detect debug (`M9081`). _params: `{id:oc}`_ |
| `setDuctWorkTimeDebug` | POST | `M9085 T${r}` → /passthrough | ? |
| `setDuctWorkTimeDebug` | SET | `M9085 T${r (fn)` | ? |
| `setDuctWorkTimeDebug` | POST | `M9085 T${r}` → /v1/parts/control | ? |
| `setDuctWorkTimeDebug` | POST | /v1/platform/accessories/control | ? _params: `{id:oc}`_ |
| `setFanBuzzer` | POST | `M9079 S${r}` → /passthrough | Duct-fan buzzer (`M9079 S<n>`). |
| `setFanBuzzer` | SET | `M9079 S${r (fn)` | Duct-fan buzzer (`M9079 S<n>`). |
| `setFanBuzzer` | POST | `M9079 S${r}` → /v1/parts/control | Duct-fan buzzer (`M9079 S<n>`). |
| `setFanBuzzer` | POST | /v1/platform/accessories/control | Duct-fan buzzer (`M9079 S<n>`). _params: `{id:ih}`_ |
| `setFanBuzzerV3` | POST | `M9079 S${r.value}` → /passthrough | ? |
| `setFanBuzzerV3` | SET | `M9079 S${r (fn)` | ? |
| `setFanBuzzerV3` | POST | `M9079 S${r.value}` → /v1/parts/control | ? |
| `setFanBuzzerV3` | POST | /v1/platform/accessories/control | ? _params: `{id:oc}`_ |
| `setFanGear` | POST | `M9064 ${r.ctr}${r.gear}` → /passthrough | Duct-fan gear (`M9064 A<n>`). |
| `setFanGear` | SET | `M9064 ${e (fn)` | Duct-fan gear (`M9064 A<n>`). |
| `setFanGear` | POST | `M9064 ${r.ctr}${r.gear}` → /v1/parts/control | Duct-fan gear (`M9064 A<n>`). |
| `setFanGear` | POST | /v1/platform/accessories/control | Duct-fan gear (`M9064 A<n>`). _params: `{id:ih}`_ |
| `setFanGearV3` | POST | `M9064 ${r.ctr}${r.gear} ${e}` → /passthrough | Duct-fan V3 gear (`M9064 A<n>`). |
| `setFanGearV3` | SET | `M9064 ${e (fn)` | Duct-fan V3 gear (`M9064 A<n>`). |
| `setFanGearV3` | POST | `M9064 ${r.ctr}${r.gear} ${e}` → /v1/parts/control | Duct-fan V3 gear (`M9064 A<n>`). |
| `setFanGearV3` | POST | /v1/platform/accessories/control | Duct-fan V3 gear (`M9064 A<n>`). _params: `{id:oc}`_ |
| `setFanSmokeExhaustTime` | POST | /config/set (port 8080) | ? |
| `setFanSmokeExhaustTime` | SET | `M7 D${r (fn)` | ? |
| `setFanSmokeExhaustTime` | PUT | /v1/device/configs | ? |
| `setFanSmokeExhaustTime` | PUT | /v1/platform/device/config | ? _params: `{}`_ |
| `setFanV3RunDuration` | POST | `M9085 T0` → /passthrough | Duct-fan V3 post-run timer (`M9085 T<sec>`). |
| `setFanV3RunDuration` | SET | `M9085 T0` | Duct-fan V3 post-run timer (`M9085 T<sec>`). |
| `setFanV3RunDuration` | POST | `M9085 T0` → /v1/parts/control | Duct-fan V3 post-run timer (`M9085 T<sec>`). |
| `setFanV3RunDuration` | POST | /v1/platform/accessories/control | Duct-fan V3 post-run timer (`M9085 T<sec>`). _params: `{id:oc}`_ |
| `setFileTransferStatus` | GET | `M325` | File transfer state (`M325 S<n>`). |
| `setFillLight` | SET | `M13` | ? |
| `setFrameStatus` | POST | `M206` | ? |
| `setGMode` | SET | `M97 S${t} (fn)` | ? |
| `setHandheldPower` | POST | /v1/project/device/accessory/control | ? _params: `{level:r.level,power:r.power}`_ |
| `setMachiningPower` | POST | /v1/project/device/accessory/control | ? _params: `{power:r.power}`_ |
| `setMultiFunctionalBaseGear` | POST | /v1/platform/accessories/control | ? _params: `{id:T1}`_ |
| `setPurifierFilterLifeDebug` | SET | `M9034 A${r (fn)` | ? |
| `setPurifierFilterLifeDebug` | POST | /v1/platform/accessories/control | ? _params: `{id:Sy}`_ |
| `setPurifierGear` | POST | `M9039 ${r}` → /passthrough | AP2 V2 purifier speed (`M9039 <gear>`). |
| `setPurifierGear` | SET | `M9039 ${r (fn)` | AP2 V2 purifier speed (`M9039 <gear>`). |
| `setPurifierGear` | POST | `M9039 ${r}` → /v1/parts/control | AP2 V2 purifier speed (`M9039 <gear>`). |
| `setPurifierGear` | POST | /v1/platform/accessories/control | AP2 V2 purifier speed (`M9039 <gear>`). _params: `{id:Sy}`_ |
| `setPurifierV3Buzzer` | POST | `M9046 F${r}` → /passthrough | Purifier V3 buzzer toggle (`M9046`). |
| `setPurifierV3Buzzer` | SET | `M9046 F${r (fn)` | Purifier V3 buzzer toggle (`M9046`). |
| `setPurifierV3Buzzer` | POST | `M9046 F${r}` → /v1/parts/control | Purifier V3 buzzer toggle (`M9046`). |
| `setPurifierV3Buzzer` | POST | /v1/platform/accessories/control | Purifier V3 buzzer toggle (`M9046`). _params: `{id:yd}`_ |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${e} A${e} B${t} C${s}` → /passthrough | ? |
| `setPurifierV3FilterLifeDebug` | SET | `M9055 W${e} (fn)` | ? |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${e} A${e} B${t} C${s}` → /v1/parts/control | ? |
| `setPurifierV3FilterLifeDebug` | POST | /v1/platform/accessories/control | ? _params: `{id:yd}`_ |
| `setPurifierV3Gear` | POST | `M9039 ${r}` → /passthrough | ? |
| `setPurifierV3Gear` | SET | `M9039 ${r (fn)` | ? |
| `setPurifierV3Gear` | POST | `M9039 ${r}` → /v1/parts/control | ? |
| `setPurifierV3Gear` | POST | /v1/platform/accessories/control | ? _params: `{id:yd}`_ |
| `setTaskId` | SET | `M810 (fn)` | ? |
| `smokingFan` | SET | `M7 N1 (fn)` | Smoking-fan state (`M7 N<0/1> D<seconds>`). |
| `socketConnNum` | — | /system?action=socket_conn_num | ? |
| `startAccessoryFirmwareUpgrade` | POST | /v1/platform/accessories/upgrade | ? |
| `startMeasure` | POST | `M311 S0` | Start single Z-probe measurement (`M311 S0`). |
| `startUpdateFirmware` | — | /burn | S1: trigger flash from previously uploaded file (`GET /burn?code=<n>`). |
| `startUpdateFirmwareNew` | — | /parts | S1: trigger accessory flash from /parts. |
| `toggleDebugMode` | SET | `M802 S${t (fn)` | ? |
| `toggleLaser` | SET | `M109 S1 (fn)` | ? |
| `toIdleMode` | SET | `M108` | Cancel any pending mode (M108). |
| `triggerReport` | POST | `M9064` → /passthrough | ? |
| `triggerReport` | SET | `M9064` | ? |
| `triggerReport` | POST | `M9064` → /v1/parts/control | ? |
| `triggerReport` | POST | /v1/platform/accessories/control | ? _params: `{id:oc}`_ |
| `updateAccessoryFirmware` | POST | /v1/parts/firmware/upgrade | ? |
| `updateFirmWareProgress` | GET | /v1/parts/firmware/upgrade-progress | Flash progress query (`/system?action=get_upgrade_progress`). |
| `updateFirmWareProgress` | — | /system?action=get_upgrade_progress | Flash progress query (`/system?action=get_upgrade_progress`). |
| `updateOptimizeFan` | POST | `M9066 A${r.gear} ${e} T${r.time||0}` → /passthrough | Trigger fan optimisation routine (`M9066`). |
| `updateOptimizeFan` | SET | `M9066 A${e (fn)` | Trigger fan optimisation routine (`M9066`). |
| `updateOptimizeFan` | POST | `M9066 A${r.gear} ${e} T${r.time||0}` → /v1/parts/control | Trigger fan optimisation routine (`M9066`). |
| `updateOptimizeFan` | POST | /v1/platform/accessories/control | Trigger fan optimisation routine (`M9066`). _params: `{id:oc}`_ |
| `uploadFirmware` | POST | /upload | S1: upload firmware blob to /upload (multipart, `filename` + `md5`); D-series + REST: family-specific. |
| `uploadFirmwareNew` | — | /parts | S1: accessory firmware upload via /parts. |
| `uploadGcode` | POST | /upload | ? |
| `version` | — | /system?action=version | Firmware version (`/system?action=version_v2` or M99). |


---

## D-series protocol (D1 / D1 Pro / D1 Pro 2.0)

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
inverse of the S1 mapping (`0/1/2`).

#### Additional endpoints (full list from D1 Pro firmware binary)

| Endpoint | Method | Purpose |
|---|---|---|
| `/index.htm` | GET | Built-in web UI |
| `/cnc/data` | GET / POST | Pause / resume / stop / receive G-code |
| `/cmd` | GET ?cmd=... / POST | Single or multi-line G-code |
| `/system` | GET ?action=… | Multi-action endpoint (mac, version, working_sta, offset, dev_name, set/get-* switches) |
| `/peripherystatus` | GET | All sensor flags (sd, tilt, moving, limit, flame mode + sensitivity) |
| `/list` / `/delete` | GET | SD-card file list / delete |
| `/read` | GET | Read raw file |
| `/spiffs` | GET | Internal flash filesystem |
| `/upload` / `/upgrade` / `/unpack` / `/updater` | POST / GET | File / firmware upload |
| `/setwifi` / `/net` | GET | Wi-Fi management |
| `/framing` | GET | Trigger framing mode |
| `/from` | GET | (unknown, possibly origin set) |
| `/ping` | GET | Liveness |
| `/tmp.gcode` / `/tran.gcode` / `/frame.gcode` | GET | Cached G-code paths (D1 Pro) |
### Status-event WebSocket on port 8081

Plain `ws://<ip>:8081/`. The device pushes single-line text frames
whenever its state changes. There is **no command channel** — every
write goes via HTTP `/cmd`.

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

The WS push channel is purely advisory — `GET /peripherystatus` and
`GET /system?action=get_working_sta` always provide the basic state if
the WS is unavailable.

### M-code reference (D-series)

D-series uses the *exact* M-code set listed in the firmware binary; XCS / xTool Studio dispatches each as `POST /cmd` (or `GET /cmd?cmd=…`).

#### Queries

| Code | Format | Effect |
|---|---|---|
| `M2000` | — | List Wi-Fi APs (returns `"ssid1" "ssid2" …`) |
| `M2002 %s` | — | Read serial number |
| `M2003 %d` | — | Device info JSON |
| `M2004 S%d` | — | Read setting key |
| `M96 N%d` | — | Get working state (replaces `/system?action=get_working_sta` for some firmwares) |
| `M99 V%s` | — | Firmware version |
| `M100 %s` | — | Device name |
| `M116 X%d Y%d` | — | Laser power info |
| `M125 / M126 X%d Y%d` | — | Work-area limits |
| `M2010 N%d S%d` | — | Read laser calibration |

#### Control

| Code | Effect |
|---|---|
| `M22 S0/S1/S2` | Resume / pause / cancel job (also `M22 S3` upgrade mode) |
| `M30 N%d` | Set fire level |
| `M66` | Query key-lock status |
| `M97 S0` / `M97 S1` | Cross-laser pointer / low-light mode |
| `M98 X%.3f Y%.3f` | Set red-cross offset |
| `M108` | Cancel job |
| `M204 X%.3f Y%.3f U%.3f` | Motion acceleration |
| `M205 X%.3f Y%.3f` | Motion velocity limits |
| `M309 N%d` | Set flame-alarm sensitivity |
| `M310 N%d` | Toggle flame alarm |
| `M311 L%d R%d U%d D%d` | Set work-area limits (left / right / up / down) |
| `M312 N%d` | Set Z-probe enabled — present in firmware, **not** wired up in the XCS / Studio app (D-series has no probe pin) |
| `M313 %f` | Z-probe reading — present in firmware, **not** wired up in the XCS / Studio app (D-series has no probe pin) |
| `M314 N%d` | Probe / measure mode (N=2/3/4 = different points) |
| `M315 N%d` | Sensor reading |
| `M316–M324` | Calibration / homing / SD-card actions (`M321` SD card, `M318` move stop, `M317` tilt stop, `M319` limit switch, `M320` X/Y point, `M323/M324` reserved) |
| `M8 N%d` | Set status mode (N1 work, N11/N13 framing modes) |
| `M2001 "%s" "%s"` | Set Wi-Fi credentials (ssid, passwd) |
| `M2006/M2007 N%d` | Per-axis enable |
| `M2009 N%.3f` | Probe height |

### Full xTool Studio D-series api inventory

Auto-extracted from `xTool Studio v3.70.90 / exts/{D1,D1Pro,D1Pro 2.0}/index.js`. The Models column shows which variants surface each api (`·` = absent).

Models column ordered as: D1, D1Pro, D1Pro 2.0.

| api | method | path / cmd | description | models |
|---|---|---|---|---|
| `airAssistV2` | POST |  → `/peripheral/airassistV2` (port 8080) | AirAssist V2 BLE control (pairing / mode switch via /peripheral/airassistV2 + M9091 family). · body: `{action:"dongo_pairing_enter"}` | D1/D1Pro/D1Pro 2.0 |
| `airAssistV2` | GET | `M499 S0 T1` | AirAssist V2 BLE control (pairing / mode switch via /peripheral/airassistV2 + M9091 family). | D1/D1Pro/D1Pro 2.0 |
| `airAssistV2` | PUT |  → `/v1/peripheral/param` | AirAssist V2 BLE control (pairing / mode switch via /peripheral/airassistV2 + M9091 family). · params: `{type:"airassistV2"}` · body: `{action:"dongo_pairing_enter"}` | D1/D1Pro/D1Pro 2.0 |
| `checkDeviceBootStatus` | GET | `checkBoot` | ? | D1/D1Pro/D1Pro 2.0 |
| `cmd` | POST |  → `/cmd` | ? | D1/D1Pro/D1Pro 2.0 |
| `cmd` | SET | `(fn)` | ? | D1/D1Pro/D1Pro 2.0 |
| `configWifi` | SET | `M2001 (fn)` | Set Wi-Fi credentials (`M2001 "<ssid>" "<pwd>"`). · body: `t` | D1/D1Pro/D1Pro 2.0 |
| `cylinder` | — |  → `vye(e` | ? | D1/·/· |
| `cylinder` | — |  → `v0e(e` | ? | ·/D1Pro/· |
| `cylinder` | — |  → `w0e(e` | ? | ·/·/D1Pro 2.0 |
| `deviceInfo` | — |  → `/system` | Full device dump (collects M2003 + many follow-ups). · params: `{action:"get_dev_name"}` · reply: custom | D1/D1Pro/D1Pro 2.0 |
| `deviceInfo` | GET | `M97` | Full device dump (collects M2003 + many follow-ups). | D1/D1Pro/D1Pro 2.0 |
| `enableOddEvenKerfGroup` | — |  → `$de` | ? | D1/·/· |
| `enableOddEvenKerfGroup` | — |  → `bfe` | ? | ·/D1Pro/· |
| `enableOddEvenKerfGroup` | — |  → `Cfe` | ? | ·/·/D1Pro 2.0 |
| `endProcessing` | — | `M108 (fn)` → `/cmd` | ? · params: `{cmd:`M108 
`}` | D1/D1Pro/D1Pro 2.0 |
| `enterProcessingMode` | SET | `M8 N11 (fn)` | ? | D1/D1Pro/D1Pro 2.0 |
| `enterWalkBorderMode` | SET | `M8 N13 (fn)` | ? | D1/D1Pro/D1Pro 2.0 |
| `framing` | — |  → `/framing` | ? | D1/D1Pro/D1Pro 2.0 |
| `getAccessoriesListViaV2Platform` | GET |  → `/v1/platform/accessories/list` | ? · params: `{}` · body: `{}` | D1/D1Pro/D1Pro 2.0 |
| `getAllDangleConnectList` | POST | `M9098` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"M9098",protocol:{type:Ge.F0F7,prefix:Kr}})}` | D1/D1Pro/D1Pro 2.0 |
| `getAllDangleConnectList` | GET | `M9098` | ? | D1/D1Pro/D1Pro 2.0 |
| `getAllDangleConnectList` | POST | `M9098` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"M9098",protocol:{type:Ge.F0F7,prefix:Kr}})}` | D1/D1Pro/D1Pro 2.0 |
| `getAllDangleConnectList` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:zo}` · body: `{command:"M9098"}` | D1/D1Pro/D1Pro 2.0 |
| `getAllDangleList` | POST | `M9092 T5000` → `/passthrough` (port 8080) | List nearby BLE accessories (`M9092 T<ms>`). | D1/D1Pro/D1Pro 2.0 |
| `getAllDangleList` | GET | `M9092 T5000` | List nearby BLE accessories (`M9092 T<ms>`). | D1/D1Pro/D1Pro 2.0 |
| `getAllDangleList` | POST | `M9092 T5000` → `/v1/parts/control` | List nearby BLE accessories (`M9092 T<ms>`). · body: `{link:"uart485",data_b64:lt({cmd:"M9092 T5000",protocol:{type:Ge.F0F7,prefix:Kr}})}` | D1/D1Pro/D1Pro 2.0 |
| `getAllDangleList` | POST |  → `/v1/platform/accessories/control` | List nearby BLE accessories (`M9092 T<ms>`). · params: `{id:zo}` · body: `{command:"M9092 T5000"}` | D1/D1Pro/D1Pro 2.0 |
| `getBackpackPurifierInfo` | POST | `M9033` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"M9033",protocol:{type:Ge.F0F7,prefix:vh}})}` | D1/D1Pro/D1Pro 2.0 |
| `getBackpackPurifierInfo` | POST | `M9033` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"M9033",protocol:{type:Ge.F0F7,prefix:vh}})}` | D1/D1Pro/D1Pro 2.0 |
| `getDangleVersion` | POST | `M99` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:Ge.F0F7,prefix:Kr}})}` | D1/D1Pro/D1Pro 2.0 |
| `getDangleVersion` | GET | `M2003` | ? | D1/D1Pro/D1Pro 2.0 |
| `getDangleVersion` | POST | `M99` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:Ge.F0F7,prefix:Kr}})}` | D1/D1Pro/D1Pro 2.0 |
| `getDangleVersion` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:zo}` · body: `{command:"M99"}` | D1/D1Pro/D1Pro 2.0 |
| `getDongleConnectStatus` | POST |  → `/device/machineInfo` (port 8080) | ? | D1/D1Pro/D1Pro 2.0 |
| `getDongleConnectStatus` | GET | `M2003` | ? | D1/D1Pro/D1Pro 2.0 |
| `getDongleConnectStatus` | GET |  → `/v1/device/machineInfo` | ? | D1/D1Pro/D1Pro 2.0 |
| `getDongleConnectStatus` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:zo}` · body: `{command:"M2003"}` | D1/D1Pro/D1Pro 2.0 |
| `getFanBootVersion` | POST | `M99` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"",protocol:{type:Ge.F0F7,prefix:[70,97,17]}})}` | D1/D1Pro/D1Pro 2.0 |
| `getFanBootVersion` | GET | `boot` | ? | D1/D1Pro/D1Pro 2.0 |
| `getFanBootVersion` | POST | `M99` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"",protocol:{type:Ge.F0F7,prefix:[70,97,17]}})}` | D1/D1Pro/D1Pro 2.0 |
| `getFanBootVersion` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:pd}` · body: `{command:"M99"}` | D1/D1Pro/D1Pro 2.0 |
| `getFanInfo` | POST | `M9082` → `/passthrough` (port 8080) | Duct-fan diagnostic snapshot (`M9082`). | D1/D1Pro/D1Pro 2.0 |
| `getFanInfo` | GET | `M9082` | Duct-fan diagnostic snapshot (`M9082`). | D1/D1Pro/D1Pro 2.0 |
| `getFanInfo` | POST | `M9082` → `/v1/parts/control` | Duct-fan diagnostic snapshot (`M9082`). | D1/D1Pro/D1Pro 2.0 |
| `getFanInfo` | POST |  → `/v1/platform/accessories/control` | Duct-fan diagnostic snapshot (`M9082`). · params: `{id:pd}` · body: `{command:"M9082"}` | D1/D1Pro/D1Pro 2.0 |
| `getFanInfoV3` | POST | `M9082` → `/passthrough` (port 8080) | ? | D1/D1Pro/D1Pro 2.0 |
| `getFanInfoV3` | GET | `M9082` | ? | D1/D1Pro/D1Pro 2.0 |
| `getFanInfoV3` | POST | `M9082` → `/v1/parts/control` | ? | D1/D1Pro/D1Pro 2.0 |
| `getFanInfoV3` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Ya}` · body: `{command:"M9082"}` | D1/D1Pro/D1Pro 2.0 |
| `getFanV3BootVersion` | POST | `(fn)` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"",protocol:{type:Ge.F0F7,prefix:[78,97,17]}})}` | D1/D1Pro/D1Pro 2.0 |
| `getFanV3BootVersion` | GET | `boot` | ? | D1/D1Pro/D1Pro 2.0 |
| `getFanV3BootVersion` | POST | `(fn)` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"",protocol:{type:Ge.F0F7,prefix:[78,97,17]}})}` | D1/D1Pro/D1Pro 2.0 |
| `getFanVersion` | GET | ` ` | ? | D1/D1Pro/D1Pro 2.0 |
| `getFanVersion` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:pd}` · body: `{command:"M99"}` | D1/D1Pro/D1Pro 2.0 |
| `getFanVersionV3` | POST | `M99` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:Ge.F0F7,prefix:vr}})}` | D1/D1Pro/D1Pro 2.0 |
| `getFanVersionV3` | GET | `M99` | ? | D1/D1Pro/D1Pro 2.0 |
| `getFanVersionV3` | POST | `M99` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:Ge.F0F7,prefix:vr}})}` | D1/D1Pro/D1Pro 2.0 |
| `getFanVersionV3` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Ya}` · body: `{command:"M99"}` | D1/D1Pro/D1Pro 2.0 |
| `getHandheldGear1Power` | POST |  → `/v1/project/device/accessory/control` | ? · params: `{level:1}` · body: `{name:dl,command:"get_power",params:{level:1}}` | D1/D1Pro/D1Pro 2.0 |
| `getHandheldGear2Power` | POST |  → `/v1/project/device/accessory/control` | ? · params: `{level:2}` · body: `{name:dl,command:"get_power",params:{level:2}}` | D1/D1Pro/D1Pro 2.0 |
| `getMachiningPower` | POST |  → `/v1/project/device/accessory/control` | ? · params: `{}` · body: `{name:dl,command:"get_machine_power",params:{}}` | D1/D1Pro/D1Pro 2.0 |
| `getMultiFunctionalBaseInfo` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:$0}` · body: `{command:"M9033"}` | D1/D1Pro/D1Pro 2.0 |
| `getNonBleAccessories` | POST |  → `/device/machineInfo` (port 8080) | ? | D1/D1Pro/D1Pro 2.0 |
| `getNonBleAccessories` | GET | `M2003` | ? | D1/D1Pro/D1Pro 2.0 |
| `getNonBleAccessories` | GET |  → `/v1/device/machineInfo` | ? | D1/D1Pro/D1Pro 2.0 |
| `getNonBleAccessoryFirmwareInfo` | POST |  → `/device/machineInfo` (port 8080) | ? | D1/D1Pro/D1Pro 2.0 |
| `getNonBleAccessoryFirmwareInfo` | GET | `M2003` | ? | D1/D1Pro/D1Pro 2.0 |
| `getNonBleAccessoryFirmwareInfo` | GET |  → `/v1/device/machineInfo` | ? | D1/D1Pro/D1Pro 2.0 |
| `getPurifierBootVersion` | POST | `M99` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"",protocol:{type:Ge.F0F7,prefix:N3}})}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierBootVersion` | GET | `boot` | ? | D1/D1Pro/D1Pro 2.0 |
| `getPurifierBootVersion` | POST | `M99` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"",protocol:{type:Ge.F0F7,prefix:N3}})}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierBootVersion` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:rm}` · body: `{command:"boot"}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierInfo` | POST | `M9033` → `/passthrough` (port 8080) | Purifier V3 status snapshot (`M9033`, BLE F0F7 framing). · body: `{link:"uart485",data_b64:lt({cmd:"M9033",protocol:{type:Ge.F0F7,prefix:sc}})}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierInfo` | GET | `M9033` | Purifier V3 status snapshot (`M9033`, BLE F0F7 framing). | D1/D1Pro/D1Pro 2.0 |
| `getPurifierInfo` | POST | `M9033` → `/v1/parts/control` | Purifier V3 status snapshot (`M9033`, BLE F0F7 framing). · body: `{link:"uart485",data_b64:lt({cmd:"M9033",protocol:{type:Ge.F0F7,prefix:sc}})}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierInfo` | POST |  → `/v1/platform/accessories/control` | Purifier V3 status snapshot (`M9033`, BLE F0F7 framing). · params: `{id:rm}` · body: `{command:"M9033"}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierInfoV3` | POST | `M9033` → `/passthrough` (port 8080) | Purifier V3 status (`M9033`). · body: `{link:"uart485",data_b64:lt({cmd:"M9033",protocol:{type:Ge.F0F7,prefix:qr}})}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierInfoV3` | GET | `M9033` | Purifier V3 status (`M9033`). | D1/D1Pro/D1Pro 2.0 |
| `getPurifierInfoV3` | POST | `M9033` → `/v1/parts/control` | Purifier V3 status (`M9033`). · body: `{link:"uart485",data_b64:lt({cmd:"M9033",protocol:{type:Ge.F0F7,prefix:qr}})}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierInfoV3` | POST |  → `/v1/platform/accessories/control` | Purifier V3 status (`M9033`). · params: `{id:ul}` · body: `{command:"M9033"}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierV3BootVersion` | POST | `M99` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:Ge.F0F7,prefix:qr}})}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierV3BootVersion` | GET | `M99` | ? | D1/D1Pro/D1Pro 2.0 |
| `getPurifierV3BootVersion` | POST | `M99` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:Ge.F0F7,prefix:qr}})}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierV3BootVersion` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:ul}` · body: `{command:"M99"}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierV3RCVersion` | POST | `M9032` → `/passthrough` (port 8080) | Purifier V3 RC firmware version (`M9032`). · body: `{link:"uart485",data_b64:lt({cmd:"M9032",protocol:{type:Ge.F0F7,prefix:qr}})}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierV3RCVersion` | GET | `M9032` | Purifier V3 RC firmware version (`M9032`). | D1/D1Pro/D1Pro 2.0 |
| `getPurifierV3RCVersion` | POST | `M9032` → `/v1/parts/control` | Purifier V3 RC firmware version (`M9032`). · body: `{link:"uart485",data_b64:lt({cmd:"M9032",protocol:{type:Ge.F0F7,prefix:qr}})}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierV3RCVersion` | POST |  → `/v1/platform/accessories/control` | Purifier V3 RC firmware version (`M9032`). · params: `{id:ul}` · body: `{command:"M9032"}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierV3Version` | POST | `M99` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:Ge.F0F7,prefix:qr}})}` | D1/D1Pro/D1Pro 2.0 |
| `getPurifierV3Version` | POST | `M99` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:Ge.F0F7,prefix:qr}})}` | D1/D1Pro/D1Pro 2.0 |
| `getSafetyFireBoxProInfo` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Mie}` · body: `{command:"M9033"}` | D1/D1Pro/D1Pro 2.0 |
| `getSDCardStatus` | — |  → `/peripherystatus` | ? · reply: custom | D1/D1Pro/D1Pro 2.0 |
| `getUltrasonicKnifeControlMode` | POST |  → `/v1/project/device/accessory/control` | ? · params: `{}` · body: `{name:dl,command:"get_control_mode",params:{}}` | D1/D1Pro/D1Pro 2.0 |
| `getWorkingSta` | — |  → `/system` | ? · params: `{action:"get_working_sta"}` · reply: custom | D1/D1Pro/D1Pro 2.0 |
| `getWorkingSta` | GET | `M96` | ? | D1/D1Pro/D1Pro 2.0 |
| `goBorder` | SET | `M17 S1` | ? | D1/D1Pro/D1Pro 2.0 |
| `laserPowerType` | — |  → `/getlaserpowertype` | ? | D1/D1Pro/D1Pro 2.0 |
| `listWifi` | GET | `M2000` | Scan available SSIDs (`M2000` or `/net/get_ap_list`). | D1/D1Pro/D1Pro 2.0 |
| `machineType` | — |  → `/getmachinetype` | ? | D1/D1Pro/D1Pro 2.0 |
| `openPowerApply` | — | `M204 X500 Y40` → `/cmd` | ? · params: `{cmd:"M204 X500 Y40"}` | D1/D1Pro/D1Pro 2.0 |
| `openPowerApply` | SET | `M204 X500 Y40` | ? | D1/D1Pro/D1Pro 2.0 |
| `openRedPoint` | — | `M97 S0` → `/cmd` | ? · params: `{cmd:"M97 S0"}` | D1/D1Pro/D1Pro 2.0 |
| `openRedPoint` | SET | `M97 S0` | ? | D1/D1Pro/D1Pro 2.0 |
| `ping` | — |  → `/ping` | ? | D1/D1Pro/D1Pro 2.0 |
| `progress` | — |  → `/progress` | ? | D1/D1Pro/D1Pro 2.0 |
| `queryKeyLockStatus` | — |  → `/system` | ? · params: `{action:"usb_key_sta"}` · reply: custom | D1/D1Pro/D1Pro 2.0 |
| `queryKeyLockStatus` | GET | `M66` | ? | D1/D1Pro/D1Pro 2.0 |
| `quitLightBurnMode` | — | `M112 N0` → `/cmd` | ? · params: `{cmd:"M112 N0"}` | D1/D1Pro/D1Pro 2.0 |
| `quitLightBurnMode` | SET | `M112 N0 (fn)` | ? | D1/D1Pro/D1Pro 2.0 |
| `resetFilterWorkTime` | POST |  → `/v1/platform/accessories/control` | Reset purifier filter timer (`M9258`). · params: `{id:$0}` | D1/D1Pro/D1Pro 2.0 |
| `resetFilterWorkTime` | POST | `M9258 ${e.data.filterType}0` → `/passthrough` (port 8080) | Reset purifier filter timer (`M9258`). | D1/D1Pro/D1Pro 2.0 |
| `resetFilterWorkTime` | POST | `M9258 ${e.filterType}0` → `/v1/parts/control` | Reset purifier filter timer (`M9258`). | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothConnect` | POST | `M9093 A${e} B1` → `/passthrough` (port 8080) | ? | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothConnect` | GET | `M9093 A${e (fn)` | ? | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothConnect` | POST | `M9093 A${e} B1` → `/v1/parts/control` | ? | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothConnect` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:zo}` | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothScanOff` | POST | `M9091 E0` → `/passthrough` (port 8080) | BLE scan off (`M9091 E0`). · body: `{link:"uart485",data_b64:lt({cmd:"M9091 E0",protocol:{type:Ge.F0F7,prefix:Kr}})}` | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothScanOff` | SET | `M9091 E0` | BLE scan off (`M9091 E0`). | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothScanOff` | POST | `M9091 E0` → `/v1/parts/control` | BLE scan off (`M9091 E0`). · body: `{link:"uart485",data_b64:lt({cmd:"M9091 E0",protocol:{type:Ge.F0F7,prefix:Kr}})}` | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothScanOff` | POST |  → `/v1/platform/accessories/control` | BLE scan off (`M9091 E0`). · params: `{id:zo}` · body: `{command:"M9091 E0"}` | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothScanOn` | POST | `M9091 E1 D180` → `/passthrough` (port 8080) | BLE scan on (`M9091 E1`). · body: `{link:"uart485",data_b64:lt({cmd:"M9091 E1 D180",protocol:{type:Ge.F0F7,prefix:Kr}})}` | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothScanOn` | SET | `M9091 E1 D180` | BLE scan on (`M9091 E1`). | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothScanOn` | POST | `M9091 E1 D180` → `/v1/parts/control` | BLE scan on (`M9091 E1`). · body: `{link:"uart485",data_b64:lt({cmd:"M9091 E1 D180",protocol:{type:Ge.F0F7,prefix:Kr}})}` | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothScanOn` | POST |  → `/v1/platform/accessories/control` | BLE scan on (`M9091 E1`). · params: `{id:zo}` · body: `{command:"M9091 E1 D180"}` | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothUnbind` | POST | `M9097 A${e}` → `/passthrough` (port 8080) | BLE forget paired device (`M9112`). | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothUnbind` | SET | `M9097 A${e (fn)` | BLE forget paired device (`M9112`). | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothUnbind` | POST | `M9097 A${e}` → `/v1/parts/control` | BLE forget paired device (`M9112`). | D1/D1Pro/D1Pro 2.0 |
| `setBluetoothUnbind` | POST |  → `/v1/platform/accessories/control` | BLE forget paired device (`M9112`). · params: `{id:zo}` | D1/D1Pro/D1Pro 2.0 |
| `setDeviceName` | — |  → `/system` | ? · params: `{action:"set_dev_name"}` | D1/D1Pro/D1Pro 2.0 |
| `setDeviceName` | SET | `M100` | ? | D1/D1Pro/D1Pro 2.0 |
| `setDuctMotorStallDebug` | POST | `M9081 A${e}` → `/passthrough` (port 8080) | Duct-fan stall-detect debug (`M9081`). | D1/D1Pro/D1Pro 2.0 |
| `setDuctMotorStallDebug` | SET | `M9081 A${e (fn)` | Duct-fan stall-detect debug (`M9081`). | D1/D1Pro/D1Pro 2.0 |
| `setDuctMotorStallDebug` | POST | `M9081 A${e}` → `/v1/parts/control` | Duct-fan stall-detect debug (`M9081`). | D1/D1Pro/D1Pro 2.0 |
| `setDuctMotorStallDebug` | POST |  → `/v1/platform/accessories/control` | Duct-fan stall-detect debug (`M9081`). · params: `{id:Ya}` | D1/D1Pro/D1Pro 2.0 |
| `setDuctWorkTimeDebug` | POST | `M9085 T${e}` → `/passthrough` (port 8080) | ? | D1/D1Pro/D1Pro 2.0 |
| `setDuctWorkTimeDebug` | SET | `M9085 T${e (fn)` | ? | D1/D1Pro/D1Pro 2.0 |
| `setDuctWorkTimeDebug` | POST | `M9085 T${e}` → `/v1/parts/control` | ? | D1/D1Pro/D1Pro 2.0 |
| `setDuctWorkTimeDebug` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Ya}` | D1/D1Pro/D1Pro 2.0 |
| `setFanBuzzer` | POST | `M9079 S${e}` → `/passthrough` (port 8080) | Duct-fan buzzer (`M9079 S<n>`). | D1/D1Pro/D1Pro 2.0 |
| `setFanBuzzer` | SET | `M9079 S${e (fn)` | Duct-fan buzzer (`M9079 S<n>`). | D1/D1Pro/D1Pro 2.0 |
| `setFanBuzzer` | POST | `M9079 S${e}` → `/v1/parts/control` | Duct-fan buzzer (`M9079 S<n>`). | D1/D1Pro/D1Pro 2.0 |
| `setFanBuzzer` | POST |  → `/v1/platform/accessories/control` | Duct-fan buzzer (`M9079 S<n>`). · params: `{id:pd}` | D1/D1Pro/D1Pro 2.0 |
| `setFanBuzzerV3` | POST | `M9079 S${e.value}` → `/passthrough` (port 8080) | ? | D1/D1Pro/D1Pro 2.0 |
| `setFanBuzzerV3` | SET | `M9079 S${e (fn)` | ? | D1/D1Pro/D1Pro 2.0 |
| `setFanBuzzerV3` | POST | `M9079 S${e.value}` → `/v1/parts/control` | ? | D1/D1Pro/D1Pro 2.0 |
| `setFanBuzzerV3` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Ya}` | D1/D1Pro/D1Pro 2.0 |
| `setFanGear` | POST | `M9064 ${e.ctr}${e.gear}` → `/passthrough` (port 8080) | Duct-fan gear (`M9064 A<n>`). | D1/D1Pro/D1Pro 2.0 |
| `setFanGear` | SET | `M9064 ${r (fn)` | Duct-fan gear (`M9064 A<n>`). · body: `r` | D1/D1Pro/D1Pro 2.0 |
| `setFanGear` | POST | `M9064 ${e.ctr}${e.gear}` → `/v1/parts/control` | Duct-fan gear (`M9064 A<n>`). | D1/D1Pro/D1Pro 2.0 |
| `setFanGear` | POST |  → `/v1/platform/accessories/control` | Duct-fan gear (`M9064 A<n>`). · params: `{id:pd}` | D1/D1Pro/D1Pro 2.0 |
| `setFanGearV3` | POST | `M9064 ${e.ctr}${e.gear} ${r}` → `/passthrough` (port 8080) | Duct-fan V3 gear (`M9064 A<n>`). | D1/D1Pro/D1Pro 2.0 |
| `setFanGearV3` | SET | `M9064 ${r (fn)` | Duct-fan V3 gear (`M9064 A<n>`). · body: `r` | D1/D1Pro/D1Pro 2.0 |
| `setFanGearV3` | POST | `M9064 ${e.ctr}${e.gear} ${r}` → `/v1/parts/control` | Duct-fan V3 gear (`M9064 A<n>`). | D1/D1Pro/D1Pro 2.0 |
| `setFanGearV3` | POST |  → `/v1/platform/accessories/control` | Duct-fan V3 gear (`M9064 A<n>`). · params: `{id:Ya}` | D1/D1Pro/D1Pro 2.0 |
| `setFanSmokeExhaustTime` | POST |  → `/config/set` (port 8080) | ? | D1/D1Pro/D1Pro 2.0 |
| `setFanSmokeExhaustTime` | SET | `M7 D${e (fn)` | ? | D1/D1Pro/D1Pro 2.0 |
| `setFanSmokeExhaustTime` | PUT |  → `/v1/device/configs` | ? | D1/D1Pro/D1Pro 2.0 |
| `setFanSmokeExhaustTime` | PUT |  → `/v1/platform/device/config` | ? · params: `{}` | D1/D1Pro/D1Pro 2.0 |
| `setFanV3RunDuration` | POST | `M9085 T0` → `/passthrough` (port 8080) | Duct-fan V3 post-run timer (`M9085 T<sec>`). | D1/D1Pro/D1Pro 2.0 |
| `setFanV3RunDuration` | SET | `M9085 T0` | Duct-fan V3 post-run timer (`M9085 T<sec>`). | D1/D1Pro/D1Pro 2.0 |
| `setFanV3RunDuration` | POST | `M9085 T0` → `/v1/parts/control` | Duct-fan V3 post-run timer (`M9085 T<sec>`). | D1/D1Pro/D1Pro 2.0 |
| `setFanV3RunDuration` | POST |  → `/v1/platform/accessories/control` | Duct-fan V3 post-run timer (`M9085 T<sec>`). · params: `{id:Ya}` · body: `{command:"M9085 T0"}` | D1/D1Pro/D1Pro 2.0 |
| `setFlameAlarm` | — | `M310` → `/cmd` | ? · params: `{cmd:"M310"}` | D1/D1Pro/D1Pro 2.0 |
| `setFlameAlarm` | SET | `M310` | ? | D1/D1Pro/D1Pro 2.0 |
| `setFlameAlarmSensitivity` | — | `M309` → `/cmd` | ? · params: `{cmd:"M309"}` | D1/D1Pro/D1Pro 2.0 |
| `setFlameAlarmSensitivity` | SET | `M309` | ? | D1/D1Pro/D1Pro 2.0 |
| `setHandheldPower` | POST |  → `/v1/project/device/accessory/control` | ? · params: `{level:e.level,power:e.power}` | D1/D1Pro/D1Pro 2.0 |
| `setLaserPower` | — | `M9` → `/cmd` | ? · params: `{cmd:"M9"}` | D1/D1Pro/D1Pro 2.0 |
| `setLaserPower` | SET | `(fn)` | ? | D1/D1Pro/D1Pro 2.0 |
| `setLimitSwitch` | — | `M319` → `/cmd` | ? · params: `{cmd:"M319"}` | D1/D1Pro/D1Pro 2.0 |
| `setLimitSwitch` | SET | `M319` | ? | D1/D1Pro/D1Pro 2.0 |
| `setMachiningPower` | POST |  → `/v1/project/device/accessory/control` | ? · params: `{power:e.power}` | D1/D1Pro/D1Pro 2.0 |
| `setMoveStop` | — | `M318` → `/cmd` | ? · params: `{cmd:"M318"}` | D1/D1Pro/D1Pro 2.0 |
| `setMoveStop` | SET | `M318` | ? | D1/D1Pro/D1Pro 2.0 |
| `setMultiFunctionalBaseGear` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:$0}` | D1/D1Pro/D1Pro 2.0 |
| `setProcessPause` | — |  → `/cnc/data` | Pause processing (`/cnc/data?action=pause`). · params: `{action:"pause"}` | D1/D1Pro/D1Pro 2.0 |
| `setProcessPause` | SET | `M22 S1` | Pause processing (`/cnc/data?action=pause`). | D1/D1Pro/D1Pro 2.0 |
| `setPurifierFilterLifeDebug` | SET | `M9034 A${e (fn)` | ? | D1/D1Pro/D1Pro 2.0 |
| `setPurifierFilterLifeDebug` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:rm}` | D1/D1Pro/D1Pro 2.0 |
| `setPurifierGear` | POST | `M9039 ${e}` → `/passthrough` (port 8080) | AP2 V2 purifier speed (`M9039 <gear>`). | D1/D1Pro/D1Pro 2.0 |
| `setPurifierGear` | SET | `M9039 ${e (fn)` | AP2 V2 purifier speed (`M9039 <gear>`). | D1/D1Pro/D1Pro 2.0 |
| `setPurifierGear` | POST | `M9039 ${e}` → `/v1/parts/control` | AP2 V2 purifier speed (`M9039 <gear>`). | D1/D1Pro/D1Pro 2.0 |
| `setPurifierGear` | POST |  → `/v1/platform/accessories/control` | AP2 V2 purifier speed (`M9039 <gear>`). · params: `{id:rm}` | D1/D1Pro/D1Pro 2.0 |
| `setPurifierV3Buzzer` | POST | `M9046 F${e}` → `/passthrough` (port 8080) | Purifier V3 buzzer toggle (`M9046`). | D1/D1Pro/D1Pro 2.0 |
| `setPurifierV3Buzzer` | SET | `M9046 F${e (fn)` | Purifier V3 buzzer toggle (`M9046`). | D1/D1Pro/D1Pro 2.0 |
| `setPurifierV3Buzzer` | POST | `M9046 F${e}` → `/v1/parts/control` | Purifier V3 buzzer toggle (`M9046`). | D1/D1Pro/D1Pro 2.0 |
| `setPurifierV3Buzzer` | POST |  → `/v1/platform/accessories/control` | Purifier V3 buzzer toggle (`M9046`). · params: `{id:ul}` | D1/D1Pro/D1Pro 2.0 |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${r} A${r} B${n} C${t}` → `/passthrough` (port 8080) | ? | D1/D1Pro/D1Pro 2.0 |
| `setPurifierV3FilterLifeDebug` | SET | `M9055 W${r} (fn)` | ? | D1/D1Pro/D1Pro 2.0 |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${r} A${r} B${n} C${t}` → `/v1/parts/control` | ? | D1/D1Pro/D1Pro 2.0 |
| `setPurifierV3FilterLifeDebug` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:ul}` | D1/D1Pro/D1Pro 2.0 |
| `setPurifierV3Gear` | POST | `M9039 ${e}` → `/passthrough` (port 8080) | ? | D1/D1Pro/D1Pro 2.0 |
| `setPurifierV3Gear` | SET | `M9039 ${e (fn)` | ? | D1/D1Pro/D1Pro 2.0 |
| `setPurifierV3Gear` | POST | `M9039 ${e}` → `/v1/parts/control` | ? | D1/D1Pro/D1Pro 2.0 |
| `setPurifierV3Gear` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:ul}` | D1/D1Pro/D1Pro 2.0 |
| `setRedCrossOffset` | — | `M98` → `/cmd` | ? · params: `{cmd:"M98"}` | D1/D1Pro/D1Pro 2.0 |
| `setRedCrossOffset` | SET | `M98` | ? | D1/D1Pro/D1Pro 2.0 |
| `setStatus` | SET | `M8` | ? | D1/D1Pro/D1Pro 2.0 |
| `setTiltStop` | — | `M317` → `/cmd` | ? · params: `{cmd:"M317"}` | D1/D1Pro/D1Pro 2.0 |
| `setTiltStop` | SET | `M317` | ? | D1/D1Pro/D1Pro 2.0 |
| `startAccessoryFirmwareUpgrade` | POST |  → `/v1/platform/accessories/upgrade` | ? | D1/D1Pro/D1Pro 2.0 |
| `stopProcessing` | — |  → `/cnc/data` | ? · params: `{action:"stop"}` | D1/D1Pro/D1Pro 2.0 |
| `stopProcessing` | SET | `M108 (fn)` | ? | D1/D1Pro/D1Pro 2.0 |
| `stopProcessMode` | SET | `M8 N1 (fn)` | ? | D1/D1Pro/D1Pro 2.0 |
| `stopWalkBorder` | — | `M108` → `/cmd` | Stop framing. · params: `{cmd:"M108"}` | D1/D1Pro/D1Pro 2.0 |
| `stopWalkBorder` | SET | `M108 (fn)` | Stop framing. | D1/D1Pro/D1Pro 2.0 |
| `system` | — |  → `/system` | ? · params: `{action:"mac"}` | D1/D1Pro/D1Pro 2.0 |
| `toLowLightMode` | — | `M97 S1` → `/cmd` | ? · params: `{cmd:"M97 S1"}` | D1/D1Pro/D1Pro 2.0 |
| `toLowLightMode` | SET | `M97 S1` | ? | D1/D1Pro/D1Pro 2.0 |
| `toRedCrossMode` | — | `M97 S0` → `/cmd` | ? · params: `{cmd:"M97 S0"}` | D1/D1Pro/D1Pro 2.0 |
| `toRedCrossMode` | SET | `M97 S0` | ? | D1/D1Pro/D1Pro 2.0 |
| `triggerReport` | POST | `M9064` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"M9064",protocol:{type:Ge.F0F7,prefix:vr}})}` | D1/D1Pro/D1Pro 2.0 |
| `triggerReport` | SET | `M9064` | ? | D1/D1Pro/D1Pro 2.0 |
| `triggerReport` | POST | `M9064` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"M9064",protocol:{type:Ge.F0F7,prefix:vr}})}` | D1/D1Pro/D1Pro 2.0 |
| `triggerReport` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Ya}` · body: `{command:"M9064"}` | D1/D1Pro/D1Pro 2.0 |
| `updateAccessoryFirmware` | POST |  → `/v1/parts/firmware/upgrade` | ? | D1/D1Pro/D1Pro 2.0 |
| `updateFirmware` | POST |  → `/upgrade` | REST family /package?action=burn (raw blob body). · reply: "OK" body | D1/D1Pro/D1Pro 2.0 |
| `updateFirmWareProgress` | GET |  → `/v1/parts/firmware/upgrade-progress` | Flash progress query (`/system?action=get_upgrade_progress`). | D1/D1Pro/D1Pro 2.0 |
| `updateOptimizeFan` | POST | `M9066 A${e.gear} ${r} T${e.time||0}` → `/passthrough` (port 8080) | Trigger fan optimisation routine (`M9066`). | D1/D1Pro/D1Pro 2.0 |
| `updateOptimizeFan` | SET | `M9066 A${r (fn)` | Trigger fan optimisation routine (`M9066`). · body: `r` | D1/D1Pro/D1Pro 2.0 |
| `updateOptimizeFan` | POST | `M9066 A${e.gear} ${r} T${e.time||0}` → `/v1/parts/control` | Trigger fan optimisation routine (`M9066`). | D1/D1Pro/D1Pro 2.0 |
| `updateOptimizeFan` | POST |  → `/v1/platform/accessories/control` | Trigger fan optimisation routine (`M9066`). · params: `{id:Ya}` | D1/D1Pro/D1Pro 2.0 |
| `uploadGcode` | POST |  → `/cnc/data` | ? · reply: custom | D1/D1Pro/D1Pro 2.0 |
| `uploadGcode` | SET | `(fn)` | ? | D1/D1Pro/D1Pro 2.0 |
| `version` | — |  → `/system` | Firmware version (`/system?action=version_v2` or M99). · params: `{action:"version"}` · reply: custom | D1/D1Pro/D1Pro 2.0 |


---

## WS-V2 protocol (TLS WebSocket RPC + push)

V2 firmware (per-model thresholds in
[WS-V2 firmware activation thresholds](#ws-v2-firmware-activation-thresholds)
above — F1 ≥ 40.51, F1 Ultra ≥ 40.52, GS003 ≥ 40.53, F2 Ultra ≥ 40.54,
GS005 ≥ 40.55, F2 ≥ 40.56, F2 Ultra Single ≥ 40.57, M1 Ultra ≥ 40.41,
P2S ≥ 40.22.011, P3 ≥ 40.23, F2 Ultra UV ≥ 40.130, MetalFab ≥ 40.70,
Apparel Printer ≥ 40.100) replaces the legacy HTTP REST transport
with a **full request/response API tunneled over three parallel TLS
WebSocket connections** on port 28900. xTool Studio calls this the
`V2` protocol (`protocolName: "V2"` in the `createV2ProtocolInstance`
factory of `atomm-sharedworker`). Older community docs described it
as "listener-only" because they observed only the broadcast event
channel — the actual API surface is full bidirectional and rivals
the legacy REST family in scope.

### Connection

Three concurrent WebSocket connections to the same endpoint, each
with a different `function=` query parameter:

```
wss://<ip>:28900/websocket?id=<timestamp>&function=instruction
wss://<ip>:28900/websocket?id=<timestamp>&function=file_stream
wss://<ip>:28900/websocket?id=<timestamp>&function=media_stream
```

- TLS with self-signed device cert (verification disabled in clients).
- `id` = `Date.now()` at connect time (millisecond timestamp), reused
  across all three connections to tie them to the same client session.
- `function=instruction` carries the JSON request/response API plus
  the unsolicited push events.
- `function=file_stream` carries firmware/G-code uploads + log
  downloads (POST blob frames with `fileType` + `fileName` query
  params; see [File transfer](#file-transfer-f1-v2)).
- `function=media_stream` carries the camera / live-preview frames.
- Heartbeat: enabled on all three with `useHeartBeat: true`. V2
  expects a heartbeat **response** (`needHeartBeatResponse: true`),
  which differs from the V1 fallback below.
- `dataStream: true` flag on V2 indicates the connection multiplexes
  multiple in-flight requests by `transactionId`.

A V1 fallback connection profile is also published in the device's
extension bundle (USB + WIFI, `useHeartBeat: true`,
`needHeartBeatResponse: false`) — used by older firmware. The
extension picks the highest mutually supported version at connect
time.

### Frame parsing

- TEXT frames: raw JSON.
- BINARY frames: device prefixes a small framing header before the
  JSON. Strategy: locate the first `{`, parse from there. A firmware
  bug occasionally double-encodes the leading byte (`{{`) — drop one
  leading byte and retry.

Two distinct frame shapes coexist on the wire:

**1. Push event (broadcast / unsolicited):**

```json
{
  "url": "<path>",
  "data": {"module": "...", "type": "...", "info": <varies>},
  "timestamp": 1700000000000
}
```

**2. Request / response (initiated by client):**

The newer V2 surface mirrors a REST API one-to-one — every named API
in the extension bundle has a `url`, `method`, optional `params`,
optional `data` (request body), optional `transformResult` (server
return shape). Frames carry a numeric `transactionId` to multiplex
concurrent calls; responses arrive on the same WS with the matching
`transactionId` (see [Connection lifecycle](#connection-lifecycle-v2)
below for the full envelope).

### Connection lifecycle (V2)

Reverse-engineered from the xTool Studio shared worker
(`atomm-sharedworker.esm.*.js`). Counters and frame templates listed
here are the live wire contract — diverging from them caused our
earlier Python implementation to silently drop every response.

**Defaults applied by Studio's worker:**

| Knob | Default | Notes |
|---|---|---|
| `connectTimeout` | 3000 ms | TLS WS open timeout. |
| `heartbeatInterval` | 3000 ms | Period between pings. |
| `heartbeatTimeout` | 11000 ms | Watchdog: close WS if no pong. |
| `useHeartBeat` | true | V2 device extension override. |
| `needHeartBeatResponse` | true | V2 expects pong; close if missing. |
| `dataStream` | true | Multiplex requests by `transactionId`. |
| `userUuid` | `mk-guest` | Default for guest sessions. |
| `socketFirstMessageCode` | `bWFrZWJsb2NrLXh0b29s` | Base64 of `makeblock-xtool`. |

DT001 (Apparel Printer) extends the WS URL with `clientId=<localStorage>&type=xcs&time=<yyyy-MM-dd HH:mm:ss>`. All other V2 devices stick to `id=<Date.now()>&function=<channel>`.

**Step 1 — Open WS:**

```
wss://<ip>:28900/websocket?id=<Date.now()>&function=instruction
wss://<ip>:28900/websocket?id=<Date.now()>&function=file_stream
wss://<ip>:28900/websocket?id=<Date.now()>&function=media_stream
```

TLS, certificate verification disabled (self-signed device cert).

**Step 2 — Parity first-message handshake:**

Right after `ws.open` and before any other request, Studio's
`sendFirstMessageCode` issues a normal V2 JSON request:

```json
{
  "type": "request",
  "method": "GET",
  "url": "/v1/user/parity",
  "params": {},
  "data": {
    "userID": "<userUuid e.g. mk-guest>",
    "userKey": "bWFrZWJsb2NrLXh0b29s",
    "timezone": "<IANA timezone>"
  },
  "timestamp": <ms>,
  "transactionId": <auto-incrementing number>
}
```

If the response carries `code !== 0` or fails, Studio closes with
`CloseCode.FirstMessageError` and does not retry until reconnect. The
old "raw text frame `bWFrZWJsb2NrLXh0b29s` after connect" claim from
older docs was a misread — the token still exists, but it lives inside
the parity request body.

**Step 3 — Request/response envelope:**

Every API call sent on `function=instruction` carries:

```json
{
  "type": "request",
  "method": "GET" | "PUT" | "POST" | "DELETE",
  "url": "/v1/...",
  "params": { ... },
  "data": { ... },
  "timestamp": <Date.now()>,
  "transactionId": <number, auto-incrementing, wraps below 65500>
}
```

Responses arrive as:

```json
{
  "type": "response",
  "code": 0,
  "transactionId": <same number — top-level OR data.transactionId>,
  "data": <object>,
  "msg": "ok"
}
```

The dispatcher reads `response.transactionId ?? response.data.transactionId`,
filters on `type === "response"`, then resolves the pending Promise.
Anything else (`type` missing, or no `transactionId`) is treated as a
push event.

**Step 4 — Heartbeat:**

Every `heartbeatInterval` (3 s) Studio sends a fixed-id ping:

```json
{
  "type": "request",
  "method": "GET",
  "url": "/v1/user/ping",
  "params": {},
  "data": {},
  "timestamp": <Date.now()>,
  "transactionId": 65510
}
```

After sending, a pong-timeout timer of `heartbeatTimeout` (11 s) fires;
if no `type:"response"` frame with `transactionId 65510` arrives the
worker closes the WS with `CloseCode.PingTimeout` and flags
`needReconnect=true`. The fixed `65510` id keeps the ping pool
disjoint from the user-request rotation (which wraps at 65500).

### V2 endpoint inventory

All paths below are sent as the `url` field of a JSON request frame
on the `instruction` WS, with the matching HTTP method in the
`method` field. Replies come back tagged with the same numeric
`transactionId` (see [Connection lifecycle](#connection-lifecycle-v2)
above for the full envelope).

#### Device info / runtime

| Path | Method | Purpose |
|---|---|---|
| `/v1/device/machineInfo` | GET | Device identity + firmware versions (returns `firmware.package_version`, `firmware.master_h3_laserservice`, …). |
| `/v1/device/runtime-infos` | GET | Live state — `{curMode:{desc,mode,subMode,taskId}}`. `mode` is one of the `P_*` enum (see below). |
| `/v1/device/configs` | GET / PUT | Persistent config blob. |
| `/v1/device/statistics` | GET | Lifetime counters. |
| `/v1/device/bind` | PUT | Pair/bind with the cloud account. |
| `/v1/env/domain` | PUT | Switch device's cloud endpoint (`atomm` / `xcs` / regional). |

#### Status / processing

| Path | Method | Purpose |
|---|---|---|
| `/v1/processing/state` | GET | Current job state. |
| `/v1/processing/progress` | GET | `{progress, workingTime, …}` for the active job. |
| `/v1/processing/upload/config` | PUT | Apply config after pushing a G-code blob (`fileType, autoStart, taskId`). |
| `/v1/processing/frame/replace` | PUT | Replace the currently-loaded framing G-code (`loopPrint, gcodeType, uMoveSpeed`). |

#### Peripherals (state via shared `/v1/peripheral/param`)

The V2 API consolidates all peripheral queries onto one path with a
`type` query param:

| `params.type` | Purpose |
|---|---|
| `ext_purifier` | External purifier status — `{current, exist, power, state}` |
| `gap` | Cover state — `{state: "on"/"off"}` (`on` = closed) |
| `machine_lock` | USB-key / safety-lock state — `{state: "on"/"off"}` |
| `airassistV2` | Air-Assist V2 BLE accessory state |
| `motion_control` | Low-level motion override |
| `ext_purifier`, `gap`, `machine_lock` are pre-fanned out as the `addonStatus` aggregate api |

Standalone peripheral paths (mostly carried over from V1):

| Path | Method | Purpose |
|---|---|---|
| `/v1/peripheral/param` | GET / PUT | Polymorphic peripheral query (typed by `params.type`). |
| `/v1/laser-head/focus/parameter` | GET / PUT | Read/write laser-head focus parameters. |
| `/v1/laser-head/focus/control` | POST | Trigger focus operation. |
| `/v1/motion_control/paramter` | GET / PUT | Motion control (typo `paramter` is the actual server path). |
| `/v1/extender/control` | POST | Toggle SafetyPro IF2 / AP2 / etc. extender. |

#### Net / Wi-Fi

| Path | Method | Purpose |
|---|---|---|
| `/v1/wifi/ap-list` | GET | List nearby SSIDs. |
| `/v1/wifi/connected-ssid` | GET | Current SSID. |
| `/v1/wifi/credentials` | PUT | Set credentials (replaces V1 `M2001`). |
| `/v1/wifi/interfaces` | GET | List interfaces. |
| `/v1/net/wifi_signal_strength` | POST `{name:"wlan0"}` | Live RSSI for a named iface. |

#### BLE accessories (parts / dongle)

| Path | Method | Purpose |
|---|---|---|
| `/v1/parts/control` | POST `{link:"uart485", data_b64:<F0F7-encoded M-code>}` | Send raw M-code (`M9091`–`M9098`, `M9032`–`M9085` …) to a BLE accessory tunneled through the dongle. |
| `/v1/parts/firmware/upgrade` | POST | Push firmware to an attached accessory. |
| `/v1/parts/firmware/upgrade-progress` | GET | Poll accessory-flash progress. |
| `/v1/platform/accessories/list` | GET | Cloud-platform accessory list. |
| `/v1/platform/accessories/control` | POST `{id:<n>, command:"<M-code>"}` | Higher-level control wrapper. |
| `/v1/platform/accessories/upgrade` | POST | Platform-mediated accessory upgrade. |
| `/v1/platform/device/config` | GET / PUT | Cloud platform config. |
| `/v1/project/accessory/list` | GET | Project-scoped accessory list. |
| `/v1/project/api/mcode` | POST | Send a raw M-code via the project API. |
| `/v1/project/device/accessory/control` | POST `{level:1\|2}` | Set accessory power level. |

#### File transfer (WS-V2)

File uploads + downloads happen on the **`function=file_stream`** WS,
not on the `instruction` channel:

| API | Method | `params` | Purpose |
|---|---|---|---|
| `/v1/filetransfer/upload` | PUT | — | Initiate upload — returns a transfer handle. |
| `/v1/filetransfer/download` | PUT | — | Initiate download. |
| `/v1/filetransfer/finish` | PUT | — | Acknowledge end-of-stream. |
| `uploadGcode` | POST blob, then PUT `/v1/processing/upload/config` | `fileType:1, fileName:"tmp.gcode"` | Upload G-code job (sequential 2-step). |
| `uploadWalkBorder` | POST blob, then PUT `/v1/processing/upload/config` | `fileType:1, fileName:"tmpFrame.gcode"` | Upload framing G-code. |
| `replaceWalkBorder` | POST blob, then PUT `/v1/processing/frame/replace` | `fileType:1, fileName:"tmpFrameNew.gcode"` | Replace framing G-code in-flight. |
| `updateFirmware` | POST blob | `fileType:2, fileName:"package.img"` | Upload firmware image. |
| `exportLog` | GET `/v1/log` then file download | `filetype:5` | Pull device log. |

The WS-V2 firmware update is itself a 3-step API:

1. `PUT /v1/device/upgrade-mode?mode=ready` with body `{machine_type:"MXF"}` — handshake, expects `{result:"ok"}`.
2. `POST` blob with `fileType:2, fileName:"package.img"` on the
   `file_stream` WS to push the firmware.
3. `PUT /v1/device/upgrade-mode?mode=upgrade` with body
   `{force_upgrade:1, action:"burn", atomm:1}` — trigger flash. Reply
   `{success:true}`.

#### Logging / debug

| Path | Method | Purpose |
|---|---|---|
| `/v1/log` | GET | Returns `{filename}` of the next available log archive (paired with a download via `file_stream`). |

### Push events

Push frames arrive on the `instruction` WS without a `transactionId`
and without `type:"response"`. Frame schema:

```json
{
  "url": "<path>",
  "data": {"module": "...", "type": "...", "info": <varies>},
  "timestamp": 1700000000000
}
```

**Modules observed:** `STATUS_CONTROLLER`, `GAP`, `MACHINE_LOCK`,
`WORK_RESULT`, `BOARDS`, `DEVID_MCODE`, `REPORT_BY_ACCESSORY_NAME`.

**Event → state mapping:**

| `url` | `module` | `type` | Notes |
|---|---|---|---|
| `/work/mode` | `STATUS_CONTROLLER` | `MODE_CHANGE` | `info.mode` is one of the `P_*` enum (table below). |
| `/device/status` | `STATUS_CONTROLLER` | `WORK_PREPARED` | `framing` when `info=="framing"` else `processing_ready`. |
| `/device/status` | `STATUS_CONTROLLER` | `WORK_STARTED` | `framing` or `processing`. |
| `/device/status` | `STATUS_CONTROLLER` | `WORK_FINISHED` | `idle` (if a framing run finished) or `finished`. |
| `/work/result` | `WORK_RESULT` | `WORK_FINISHED` | Captures `info.timeUse`, `info.taskId`. |
| `/gap/status` | `GAP` | `OPEN`/`CLOSE` | Cover state. |
| `/machine_lock/status` | `MACHINE_LOCK` | `OPEN`/`CLOSE` | LOCK device class — `OPEN`=unlocked, `CLOSE`=locked. |

### `P_*` mode enum (V2 work-state)

Used in `/v1/device/runtime-infos.curMode.mode` and the
`/work/mode → MODE_CHANGE` push:

| Mode | Meaning |
|---|---|
| `P_BOOT` | Device booting / not yet ready |
| `P_SLEEP` | Sleep / standby |
| `P_IDLE` | Idle |
| `P_READY` | Ready (older firmware) |
| `P_WORK` | Online job ready |
| `P_ONLINE_READY_WORK` | Online job loaded, awaiting start |
| `P_OFFLINE_READY_WORK` | Offline (button-mode) job loaded |
| `P_WORKING` | Actively processing |
| `P_WORK_DONE` | Job completed (transient) |
| `P_FINISH` | Finished |
| `P_MEASURE` | Measure / probe in progress |
| `P_UPGRADE` | Firmware upgrade in progress |
| `P_ERROR` | Error state |

`subMode` carries the working-mode classifier (e.g. `LASER_PLANE`,
`KNIFE_CUT`, `INK_PRINT`, `DTF_PRINT`, `ROTATE_ATTACHMENT`,
`CURVE_PROCESS`, …) — the full enum has ~40 entries reflecting every
job type the WS-V2 family (F1, F1 Ultra, F2 family, M1 Ultra, P2S,
P3, MetalFab, Apparel Printer, …) can run.

### Behaviour matrix (per-firmware overrides)

Some V2 device behaviour is gated by a per-model + per-firmware map
(`base + per-model overrides`). Observed flags:

| Flag | Default | Override examples |
|---|---|---|
| `wifiSetLimit` | `true` | DT001 firmware `40.100.009.00` → `false` |
| `wifiStrength` | `false` | DT001 firmware `40.100.009.00` → `true`; HJ003 some firmware → `true` |
| `heartbeat` | `false` | DT001 firmware `40.100.009.00` → `true`; HJ003 firmware `40.70.006.2020` → `true` |

Older community docs (pre xTool Studio audit) described WS-V2 as
listener-only because the bundled extension only shipped a fixed set
of push handlers — but the `function=instruction` channel in fact
accepts the full V2 request
schema. Implementations that only consume the broadcast channel will
see status / gap / lock / work-result events but miss the rich query
+ control surface above.


---

## REST API family (F1 / F1 Ultra / F1 Ultra V2 / F1 Lite / F2 / F2 Ultra / F2 Ultra Single / F2 Ultra UV / M1 / M1 Ultra / MetalFab / P1 / P2 / P2S / P3 / Apparel Printer)

JSON over HTTP. Verified against the per-model `index.js` bundles in the XCS APK and the newer xTool Studio Windows app (`exts.zip/<model>/index.js`).

### Ports

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

### Firmware endpoints (port 8087)

| Endpoint | Method | Notes |
|---|---|---|
| `/upgrade_version?force_upgrade=1[&machine_type=<code>]` | GET | Handshake. `machine_type` per model — see [Cloud content IDs](#cloud-content-ids-and-machine_type-per-model). |
| `/package?action=burn` | POST raw blob | Upload + flash main firmware. |
| `/script` | POST raw blob | Upload firmware script (M1 four-step flow only). |
| `/burn?reboot=true` | POST | Trigger reboot after script + package upload (M1 only). |

Full per-family flash sequence is documented under
[Firmware update protocol → Flash flow](#flash-flow).

### Camera on port 8329

P2/P2S/F1/F1 Ultra:

| Endpoint | Method | Notes |
|---|---|---|
| `/camera/snap?stream=0` | GET, blob | Global / overview camera |
| `/camera/snap?stream=1` | GET, blob | Local / close-up camera |
| `/camera/exposure?stream=0/1` | POST `{value:<int>}` | Set exposure |
| `/camera/fireRecord` | POST, blob | Recorded flame snapshot (F1 Ultra) |


### Endpoint map (Linux `laserservice` HTTP daemon, port 8080)

The Linux-based REST family shares the same `laserservice` HTTP daemon, but each model exposes a slightly different subset. The tables below catalogue every path observed in the binary across all REST models.

#### Status & control

| Path | Notes |
|---|---|
| `/cnc/status` | Live status (mode + subMode) |
| `/cnc/data` | G-code stream / job pump |
| `/cnc/data_owner` | Job owner — used to detect XCS-vs-mobile conflict |
| `/cnc/cmd` | One-shot G-code |
| `/cnc/light` | Built-in fill-light bri (`{action:set_bri,value}`) |
| `/cnc/fan` | Cooling fan control |
| `/cnc/reset` | Soft reset |
| `/cnc/resetfirmware` | Reset MCU firmware |
| `/system` | mac / version / sn / dev_name (same as D-series) |
| `/peripherystatus` | Aggregated peripheral state |
| `/peripherals` / `/parts` / `/partsProgress` | Multi-part / kit accessory job info |

#### Device info / mode

| Path | Notes |
|---|---|
| `/device/machineInfo` | Returns `{deviceName,sn,mac,ip,laserPower,firmware,…}` |
| `/device/runningStatus` | Job running mode JSON |
| `/device/workingInfo` | `{taskId}` plus job stats |
| `/device/modeSwitch` | Toggle laser mode (cut / engrave / dot etc.) |
| `/device/upgrade` | Firmware OTA |
| `/getmachineID` / `/getmachineinfo` / `/getmachinetype` | Various ID/info paths (some redundant for legacy clients) |
| `/gethardwaretype` | Hardware revision |
| `/getmode` / `/setmode` | Working mode get/set |
| `/getofflinemode` / `/setofflinemode` | Offline button-button mode |
| `/getprintToolType` / `/setprintToolType` | Tool type (laser, knife, …) |

#### Peripherals (each `/peripheral/<x>` accepts `?action=get` for state)

| Path | Notes |
|---|---|
| `/peripheral/fill_light` | Brightness 0-255 (REST integer scale) |
| `/peripheral/ir_led` | IR LEDs (close-up + global on P2) |
| `/peripheral/digital_lock` | Cover digital lock |
| `/peripheral/gap` | Cover open detection |
| `/peripheral/drawer` | Front-drawer position |
| `/peripheral/laser_head` | Coordinate query / move |
| `/peripheral/ir_measure_distance` | IR distance probe |
| `/peripheral/quest/ir_measure_distance` | Same as above with averaging |
| `/peripheral/gyro` | 3-axis accelerometer (`gyro_x/y/z`) |
| `/peripheral/beep` | Buzzer toggle / pattern |
| `/peripheral/button` | Last physical button event (short / long / long-long / double) |
| `/peripheral/cooling_fan` | CPU + laser cooling |
| `/peripheral/smoking_fan` | Smoke extraction |
| `/peripheral/air_pump` | V1 air-pump |
| `/peripheral/airassist` | V2 Air-Assist on/off (M1 Ultra only) |
| `/peripheral/airassistV2` | V2 Air-Assist Bluetooth pairing |
| `/peripheral/ext_purifier` | External purifier |
| `/peripheral/fire_extinguisher` / `/peripheral/fire_extinguisherV1_5` | Fire suppressor (two HW revisions) |
| `/peripheral/fire_sensor` / `/peripheral/uv_fire_sensor` | UV-based + IR-based fire detect |
| `/peripheral/water_flow` / `/peripheral/water_pump` / `/peripheral/water_tmp` / `/peripheral/water_line` | Water cooling — F1 Ultra fiber laser |
| `/peripheral/machine_lock` | Lock state |
| `/peripheral/digital_screen` | Built-in display |
| `/peripheral/camera_power` | Camera power |
| `/peripheral/motion_control` | Low-level motion override |
| `/peripheral/ui_led` | Front status LED ring |
| `/peripheral/led_on_board` | M1 Ultra board LEDs |
| `/peripheral/Z_ntc_temp` / `/peripheral/Z_firedetect` / `/peripheral/Z_firedetect_temp` | M1 Ultra Z-axis temp & fire sensors |
| `/peripheral/heighten` | M1 Ultra raise/lower stage |
| `/peripheral/conveyor` | F1 Ultra conveyor accessory |
| `/peripheral/inkjet_printer` | M1 Ultra inkjet head |
| `/peripheral/knife_cut_plate` / `/peripheral/knife_head` | M1 Ultra knife head accessory |
| `/peripheral/coaxial_Ir` | P2S coaxial IR |
| `/peripheral/flame_process` | P2S flame-handling state |
| `/peripheral/encoder` | M1 Ultra rotary encoder |
| `/peripheral/ultrason` | M1 Ultra ultrasonic probe |
| `/peripheral/attitude` | M1 Ultra IMU attitude vector |
| `/peripheral/adsorption_mat` | M1 Ultra vacuum bed |
| `/peripheral/calibrate_area` | Calibration area |
| `/peripheral/crossred` / `/peripheral/crossred_Offset` | Cross-laser pointer + offset |
| `/peripheral/laser_height_offset` | Z height calibration |
| `/peripheral/workhead_ID` / `/peripheral/workhead_ZHeight` / `/peripheral/workhead_Zchange` | Workhead identity / height |
| `/peripheral/z_tmc_current` | Z-axis stepper current |
| `/peripheral/position` | Job-absolute position |

#### Camera / measure (P2 / P2S / F1 Ultra)

| Path | Notes |
|---|---|
| `/camera/snap?stream=<0/1>` | JPEG snapshot (port **8329**) |
| `/camera/exposure?stream=<0/1>` | Exposure config |
| `/camera/fireRecord` | Last flame snapshot (F1 Ultra) |
| `/measure/getDistance` | IR-distance probe result |
| `/measure/circleCode` / `/measure/qrCode` | Calibration codes |
| `/measure/recogniseProfile` | Auto-profile detection |
| `/opencamera` / `/openir` / `/openelock` | Power gates for camera, IR LEDs and electronic lock |

#### Job / processing

| Path | Notes |
|---|---|
| `/processing/start` / `/pause` / `/resume` / `/stop` / `/restart` / `/replace` | Job control |
| `/processing/upload` | Push G-code |
| `/processing/download` | Pull G-code |
| `/processing/progress` | Poll `{progress,workingTime,…}` |
| `/processing/print_type` | Vector / raster / mixed |
| `/processing/batch` / `/processing/backup` / `/processing/powerResume` / `/processing/worktime` | F1 Ultra extras |
| `/parts` / `/partsProgress` | Multi-part jobs |

#### Firmware / config / debug

| Path | Notes |
|---|---|
| `/firmware/handshake` | Pre-flash handshake (replaces port 8087 path on newer FW) |
| `/firmware/upgradeAll` | Multi-MCU upgrade trigger |
| `/config/get` (POST `{type:"user",kv:[…]}`) | Read user config keys (e.g. `airassistCut/Grave`, `EXTPurifierTimeout`, `purifierSpeed`, `beepEnable`, `flameLevelHLSelect`) |
| `/config/set` (POST same shape) | Write user config |
| `/config/operate` / `/config/resume` / `/config/back_to_factory` / `/config/reset_to_factory` / `/config/delete` | Factory reset / recovery |
| `/alarm/control` / `/alarm/getRecord` | Alarm enable + history |
| `/extender/control` | Toggle SafetyPro IF2 / AP2 |
| `/focus/control` | Auto-focus |
| `/debug/loglevel` / `/debug/running` | Runtime debug |
| `/setdate` | Set device clock |
| `/reboot` / `/sleepwakeup` | Power management |
| `/headbackhome` | Home laser head |
| `/recoveryfactory` / `/backupfactory` / `/recoveryCamCali` / `/recoveryCutoffsiteCali` / `/recoveryIrCali` / `/recoveryMotionoffsiteCali` / `/recoveryConfigKeyValue` | Factory backup / restore |
| `/openCPUFan` | M1 only: force CPU fan |
| `/simulate_open_door` / `/simulate_close_door` / `/simulate_press_button` / `/simulate_alarm` / `/simulate_fire` | F1 Ultra: hardware-event simulator (testing) |
| `/passthrough` | Raw G-code → MCU |
| `/time/sync` | NTP-style time sync |
| `/setBeepEnable` / `/getBeepEnable` | Buzzer toggle |
| `/setFilllightAutoClosetimout` / `/getFilllightAutoClosetimout` | Fill-light auto-off |
| `/setIrlightAutoClosetimout` / `/getIrlightAutoClosetimout` | IR-light auto-off |
| `/setsleeptimeout` / `/getsleeptimeout` | Idle sleep timeout |
| `/setsleeptimeoutopengap` / `/getsleeptimeoutopengap` | Sleep timeout when cover open |
| `/getlaserpower` / `/setlaserpower` / `/getlaserpowertype` / `/setlaserpowertype` | Laser power info |
| `/getfilllight` / `/setfilllight` | Fill-light brightness |
| `/getinfraredlight` / `/setinfraredlight` | IR illumination |
| `/getdotlaserpower` / `/setdotlaserpower` | Red-dot pointer power (M1) |
| `/getdrawercheck` / `/setdrawercheck` | Drawer-presence enforcement |
| `/getfiltercheck` / `/setfiltercheck` | Filter-presence enforcement (purifier) |
| `/getpurifiercheck` / `/setpurifiercheck` / `/getpurifiercontinue` / `/setpurifiercontinue` | Purifier-state + auto-continue |
| `/getheadhomestatus` / `/headbackhome` | Home status / trigger home |
| `/get_status` | Aggregated heartbeat |

### REST status mapping

The REST `/cnc/status` JSON uses different codes than the S1's M222.
Both can be normalised onto the same status set with a small lookup
table (see `_REST_STATUS_MAP` in the bundles).

### Full xTool Studio REST-family api inventory

Auto-extracted from `xTool Studio v3.70.90 / exts/<model>/index.js` for every REST device. The Models column shows which devices surface each api (`·` = absent). Many entries are duplicated across legacy (direct REST) and v1 (`/v1/...` newer) endpoints — the legacy paths are the historical wire-level interface, the v1 paths are added by newer firmware.

Models column ordered as: F1, F1Ultra, GS003, GS005, GS006, GS004-CLASS-4, GS007-CLASS-4, GS009-CLASS-4, M1, M1Ultra, P1, P2, P2S, P3, HJ003, DT001.

| api | method | path / cmd | description | models |
|---|---|---|---|---|
| `addonStatus` | GET |  → `/v1/peripheral/param` | ? · params: `{type:"ext_purifier"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `addonStatus` | — |  → `/peripheral/ext_purifier` | ? | F1/F1Ultra/GS003/GS005/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `addonStatus` | — |  → `/peripheral/gap` | ? | ·/·/·/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `airAssistV2` | POST |  → `/peripheral/airassistV2` (port 8080) | AirAssist V2 BLE control (pairing / mode switch via /peripheral/airassistV2 + M9091 family). · body: `{action:"dongo_pairing_enter"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `airAssistV2` | GET | `M499 S0 T1` | AirAssist V2 BLE control (pairing / mode switch via /peripheral/airassistV2 + M9091 family). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `airAssistV2` | PUT |  → `/v1/peripheral/param` | AirAssist V2 BLE control (pairing / mode switch via /peripheral/airassistV2 + M9091 family). · params: `{type:"airassistV2"}` · body: `{action:"dongo_pairing_enter"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `autoCaptureLImage` | GET |  → `/v1/camera/algorithm` (port 8329) | ? · params: `{function:"autoNearAdjust"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `autoCaptureLImage` | — |  → `/algorithm` (port 8329) | ? · params: `{function:"autoNearAdjust"}` · reply: custom | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `autocheckCache` | — |  → `/v1/camera/autocheckCache` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `autocheckCache` | GET |  → `/autocheckCache` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `autoMeasure` | — |  → `e.ext.commonResource.thicknessImg` | ? | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `autoMeasure` | — |  → `t.ext.commonResource.thicknessImg` | ? | ·/·/·/·/GS006/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `backupFiles` | POST |  → `/v1/file-backups` | ? | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `backupFiles` | POST |  → `/processing/backup` | ? | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `batchMode` | PUT |  → `/v1/processing/batch` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `batchMode` | — |  → `/processing/batch` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `calibrateCamera` | — |  → `/v1/camera/algorithm` (port 8329) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `calibrateCamera` | — |  → `/algorithm` (port 8329) | ? · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `cameraNearSnap` | GET |  → `/v1/camera/image` (port 8329) | ? · params: `{filename:a.filename}` · body: `{stream:"near"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `cameraNearSnap` | — |  → `/snap?stream=1` (port 8329) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `cameraUpsideSnap` | GET |  → `/v1/camera/image` (port 8329) | ? · params: `{filename:a.filename}` · body: `{stream:"upside"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `cameraUpsideSnap` | — |  → `/snap?stream=upside` (port 8329) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `cancelPrint` | PUT |  → `/v1/processing/state` | Cancel job (REST). · params: `{action:"stop"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/P2S/P3/HJ003/· |
| `cancelPrint` | GET |  → `/processing/stop` | Cancel job (REST). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/P2/P2S/P3/HJ003/DT001 |
| `cancelPrint` | — |  → `/cnc/data?action=stop` | Cancel job (REST). | ·/·/·/·/·/·/·/·/M1/·/P1/·/·/·/·/· |
| `cancelPrint` | POST |  → `/v1/processing/stop` | Cancel job (REST). | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `cancelTask` | GET |  → `/v1/task/cancel` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `cancelTask` | POST |  → `/task/cancel` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `capture` | — |  → `n.ext.commonResource.refreshBgImg` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `capture` | — |  → `r.ext.commonResource.refreshBgImg` | ? | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `capture` | — |  → `e.ext.commonResource.refreshBgImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `capture` | — |  → `e.ext.commonResource.closeUpPhotoImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `capture` | — |  → `a.commonResource.refreshBgImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `capture` | — |  → `a.commonResource.closeUpPhotoImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `captureClose` | — |  → `e.value.commonResource.closeUpPhotoImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `captureCloseStitch` | — |  → `e.value.commonResource.closePhotocollageImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `captureFar` | — |  → `e.value.commonResource.refreshBgImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `captureGImage` | GET |  → `/v1/camera/algorithm` (port 8329) | ? · params: `{filename:a.filename}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `captureGImage` | — |  → `/algorithm` (port 8329) | ? · reply: custom | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `captureGlobalImage` | GET |  → `/v1/camera/image` | Camera snap stream 0 (`/camera/snap?stream=0`). · params: `{name:"main"}` · body: `{width:"4656",height:"3496"}` | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `captureGlobalImage` | — |  → `/camera/snap` (port 8329) | Camera snap stream 0 (`/camera/snap?stream=0`). · params: `{width:"4656",height:"3496"}` | ·/F1Ultra/GS003/·/GS006/·/·/·/·/·/·/P2/P2S/·/HJ003/· |
| `captureGlobalImage` | GET |  → `/v1/camera/snap` | Camera snap stream 0 (`/camera/snap?stream=0`). · params: `{name:"main"}` | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/HJ003/· |
| `captureGlobalImage` | — |  → `/snap` (port 8329) | Camera snap stream 0 (`/camera/snap?stream=0`). | ·/·/·/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `captureGroup` | — |  → `e.ext.commonResource.refreshBgImg` | ? | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/·/·/·/·/P2/P2S/·/·/· |
| `captureGroup` | — |  → `t.ext.commonResource.refreshBgImg` | ? | ·/·/·/·/GS006/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `captureGroup` | — |  → `a.commonResource.refreshBgImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `captureImage` | — |  → `/snap` (port 8329) | ? · params: `{stream:"0"}` | ·/·/·/·/·/·/·/·/M1/·/P1/·/·/·/·/· |
| `captureLImage` | GET |  → `/v1/camera/algorithm` (port 8329) | ? · params: `{filename:a.filename}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `captureLImage` | — |  → `/algorithm` (port 8329) | ? · params: `{function:"nearAdjust"}` · reply: custom | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `captureLiveImage` | — |  → `/camera/live` (port 8329) | Live camera frame. | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `captureLiveImage` | GET |  → `/v1/camera/live` (port 8329) | Live camera frame. | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `captureLocalImage` | — |  → `/camera/snap` (port 8329) | Camera snap stream 1 (`/camera/snap?stream=1`). · params: `{stream:"1"}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/HJ003/· |
| `captureLocalImage` | GET |  → `/v1/camera/image` | Camera snap stream 1 (`/camera/snap?stream=1`). · params: `{filename:i.filename}` · body: `{stream:"1"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `captureLocalImage` | GET |  → `/v1/camera/snap` (port 8329) | Camera snap stream 1 (`/camera/snap?stream=1`). · params: `{name:"deep"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `checkDeviceBootStatus` | GET | `checkBoot` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `cleanPrinterHead` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"clean"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `cleanPrintHead` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"inkjet_printer"}` · body: `{action:"spit"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `cleanPrintHead` | POST |  → `/peripheral/inkjet_printer` | ? · body: `{action:"spit"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `cleanProcessingProgressCache` | — | `(fn)` → `/cnc/cmd?cmd=M88 S0` | ? · reply: custom | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `clearProgress` | — | `(fn)` → `/cnc/cmd?cmd=M88 S0` | Reset progress counter (`M88 S0`). · reply: custom | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `closeNozzleCheck` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"test_for_print_demo",debug:!1}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `closePrinterRedCross` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"set_red_light",status:"off"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `colorOfInkVolume` | — |  → `ZAe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `command` | POST |  → `/v1/cnc/cmd` | ? | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/P2S/·/·/· |
| `command` | — |  → `/cmd` | ? | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `command` | — |  → `/cnc/cmd` | ? | ·/·/·/·/·/·/·/·/M1/M1Ultra/P1/P2/P2S/P3/·/· |
| `command` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"laserPointBurn"}` · body: `{action:"on",power:500,time:25}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `completePrintCalib` | POST |  → `/v1/calibration/complete` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `completePrintCalib` | POST |  → `/calibration/complete` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `configWifi` | PUT |  → `/v1/wifi/credentials` | Set Wi-Fi credentials (`M2001 "<ssid>" "<pwd>"`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/P2S/P3/HJ003/· |
| `configWifi` | POST |  → `/net/set_wifi` | Set Wi-Fi credentials (`M2001 "<ssid>" "<pwd>"`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/P2/P2S/P3/HJ003/DT001 |
| `configWifi` | POST |  → `/net` | Set Wi-Fi credentials (`M2001 "<ssid>" "<pwd>"`). · params: `{action:"connsta"}` | ·/·/·/·/·/·/·/·/M1/·/P1/·/·/·/·/· |
| `connectInfo` | — |  → `/v1/peripheral/param` | ? · params: `{type:"heighten"}` · body: `{action:"get"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `connectInfo` | POST |  → `/peripheral/heighten` | ? · params: `{action:"get"}` · body: `{action:"get"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `ContentNode` | — |  → `jf(_.url)` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `controlExtender` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"conveyor"}` | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `controlExtender` | POST |  → `/peripheral/conveyor` | ? | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `controlLaserHead` | PUT |  → `/v1/laser-head/parameter` | ? | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `controlLaserHead` | POST |  → `/peripheral/laser_head` | ? | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `controlLed` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"ir_led"}` | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `controlLed` | POST |  → `/peripheral/ir_led` | ? | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `controlRedLed` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"ir_led"}` | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `controlRedLed` | POST |  → `/peripheral/ir_led` | ? | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `correctProcessTime` | — |  → `/cnc/data` | ? · params: `{query:"progress"}` | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `createTask` | POST |  → `/v1/task/create` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `createTask` | POST |  → `/task/create` | ? · reply: custom | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `curveGroup` | — |  → `e.ext.commonResource.fCurveMeasureImg` | ? | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `curveGroup` | — |  → `t.ext.commonResource.fCurveMeasureImg` | ? | ·/·/·/·/GS006/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `curveMeasurement` | — |  → `e.ext.commonResource.fCurveMeasureImg` | ? | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `curveMeasurement` | — |  → `t.ext.commonResource.fCurveMeasureImg` | ? | ·/·/·/·/GS006/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `curvePreview` | — |  → `e.ext.commonResource.pCurveMeasureImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `curvePreview` | — |  → `ZKe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `cuttingHeightSetting` | — |  → `YIe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `cylinder` | — |  → `jye(e` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `cylinder` | — |  → `hEe(e` | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `cylinder` | — |  → `X3e(n` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/·/·/·/· |
| `deleteVideo` | — |  → `/v1/recorddel` (port 8089) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `delFireMsg` | POST |  → `/dev/operateRecord` | ? · body: `{action:"delFireMsg"}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `delFireMsg` | POST |  → `/v1/device/operate-log` | ? · body: `{action:"delFireMsg"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `delFireMsg` | POST |  → `/dev/firelog` | ? · body: `{action:"clean"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `density` | — |  → `AA` | ? | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `density` | — |  → `i$` | ? | ·/·/·/·/·/GS004-CLASS-4/·/·/·/·/·/·/·/·/·/· |
| `density` | — |  → `r$` | ? | ·/·/·/·/·/·/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `density` | — |  → `X3e` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `density` | — |  → `lbe` | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `deviceInfo` | GET |  → `/v1/device/machineInfo` | Full device dump (collects M2003 + many follow-ups). · params: `{type:"airassist"}` · body: `{alias:"config",type:"user",kv:["fillLightBrightness","purifierTimeout","workingMode","flameLevelHLSelect","airassistCut","airassistGrave","EXTPurifierTimeout","purifierSpeed","purifierBlockAlarm","beepEnable","taskId","adsorptionMatAutoControl","isAbnormalShakingMachine","flameLevel1ValueH","flameLevel1ValueL"]}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/P2S/P3/·/DT001 |
| `deviceInfo` | POST |  → `/device/machineInfo` | Full device dump (collects M2003 + many follow-ups). · params: `{action:"get"}` · body: `{alias:"config",type:"user",kv:["fillLightBrightness","purifierTimeout","workingMode","flameLevelHLSelect","airassistCut","airassistGrave","EXTPurifierTimeout","purifierSpeed","purifierBlockAlarm","beepEnable","taskId","adsorptionMatAutoControl","isAbnormalShakingMachine","flameLevel1ValueH","flameLevel1ValueL"]}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/P2/P2S/P3/·/DT001 |
| `deviceInfo` | — |  → `/system` | Full device dump (collects M2003 + many follow-ups). · params: `{action:"get_dev_name"}` · reply: custom | ·/·/·/·/·/·/·/·/M1/·/P1/·/·/·/·/· |
| `deviceInfo` | POST |  → `/device/workingInfo` | Full device dump (collects M2003 + many follow-ups). · body: `{alias:"config",type:"user",kv:["offlineProtoModeEnable","offlineProtoProModeEnable","offlineOptionModeEnable","flameAlarm","flameSensitivity","fillLightBrightness","purifierTimeout","motionPowerCut","laserMeasureOffset","flameLevelHLSelect","purifierEnable","beepEnable","taskId","laserFocus"]}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `deviceInfo` | GET |  → `/v1/device/statistics` | Full device dump (collects M2003 + many follow-ups). · body: `{alias:"config",type:"user",kv:["offlineProtoModeEnable","offlineProtoProModeEnable","offlineOptionModeEnable","flameAlarm","flameSensitivity","fillLightBrightness","purifierTimeout","motionPowerCut","laserMeasureOffset","flameLevelHLSelect","purifierEnable","beepEnable","taskId","laserFocus"]}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `deviceSleep` | POST |  → `/v1/device/sleep` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `deviceSleep` | POST |  → `/device/sleep` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `deviceWakeup` | POST |  → `/v1/device/wakeup` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `deviceWakeup` | POST |  → `/device/wakeup` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `directStartProcess` | — |  → `/cnc/data` | ? · params: `{action:"start"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `directStartProcess` | GET |  → `/v1/cnc/data` | ? · params: `{action:"start"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `disableLaserHead` | POST |  → `/peripherals` | ? · params: `{type:"LaserHead",action:"Laser_disable"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `disableLaserHead` | POST |  → `/v1/peripherals` | ? · params: `{type:"LaserHead"}` · body: `{action:"Laser_disable"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `displayControl` | POST |  → `/v1/display/control` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `displayControl` | POST |  → `/display/control` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `distence` | — |  → `r.ext.value.commonResource.focalLengthImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `doubleYLeveling` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"laser_head"}` · body: `{action:"doubleYLeveling"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `doubleYLeveling` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"doubleYLeveling"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `downLoadConfig` | — |  → `/file` (port 8080) | ? · params: `{action:"download"}` | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `downLoadFile` | — |  → `/file` (port 8080) | ? · params: `{action:"download"}` | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `downLoadVideo` | — |  → `v1/recordplay` (port 8089) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `drawerClose` | — |  → `/peripheral/drawer` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `drawerClose` | GET |  → `/v1/peripheral/param` | ? · params: `{type:"drawer"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `drawerStatus` | — |  → `/peripheral/drawer` | Drawer open/closed (`/peripheral/drawer`). | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/P3/·/· |
| `drawerStatus` | GET |  → `/v1/peripheral/param` | Drawer open/closed (`/peripheral/drawer`). · params: `{type:"drawer"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `drilling_enable` | — |  → `jNe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `emergencyStopRecover` | POST |  → `/emergency_stop` (port 8080) | ? · body: `{action:"resume"}` · reply: custom | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `emergencyStopRecover` | POST |  → `/v1/emergency_stop` | ? · body: `{action:"resume"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `enableFlyCut` | — |  → `ENe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `enableGroupCenter` | — |  → `_Ne` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `enableLaserHead` | POST |  → `/peripherals` | ? · params: `{type:"LaserHead",action:"Laser_enable"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `enableLaserHead` | POST |  → `/v1/peripherals` | ? · params: `{type:"LaserHead"}` · body: `{action:"Laser_enable"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `enableOddEvenKerfGroup` | — |  → `Gue` | ? | F1/·/·/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `enableOddEvenKerfGroup` | — |  → `n9e` | ? | ·/F1Ultra/·/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `enableOddEvenKerfGroup` | — |  → `w8e` | ? | ·/·/GS003/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `enableOddEvenKerfGroup` | — |  → `$ge` | ? | ·/·/·/GS005/·/·/·/·/·/·/·/·/·/·/·/· |
| `enableOddEvenKerfGroup` | — |  → `U1e` | ? | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `enableOddEvenKerfGroup` | — |  → `t2e` | ? | ·/·/·/·/·/GS004-CLASS-4/·/·/·/·/·/·/·/·/·/· |
| `enableOddEvenKerfGroup` | — |  → `i2e` | ? | ·/·/·/·/·/·/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `enableOddEvenKerfGroup` | — |  → `w3e` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `enableOddEvenKerfGroup` | — |  → `lpe` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `enableOddEvenKerfGroup` | — |  → `mbe` | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `enableOddEvenKerfGroup` | — |  → `tde` | ? | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `enableOddEvenKerfGroup` | — |  → `iRe` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/·/·/·/· |
| `enableOddEvenKerfGroup` | — |  → `KIe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `enableOddEvenKerfGroup` | — |  → `jPe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `enableOddEvenKerfGroup` | — |  → `Hwe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `enterCalibMode` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"calibrate_area"}` · body: `{action:"enter"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `enterCalibMode` | POST |  → `/peripheral/calibrate_area` | ? · body: `{action:"enter"}` · reply: custom | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `enterPrintClean` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"inkjet_printer"}` · body: `{action:"spit_enter"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `enterPrintClean` | POST |  → `/peripheral/inkjet_printer` | ? · body: `{action:"spit_enter"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `executeBind` | PUT |  → `/v1/device/bind` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `exitCalibMode` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"calibrate_area"}` · body: `{action:"exit"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `exitCalibMode` | POST |  → `/peripheral/calibrate_area` | ? · body: `{action:"exit"}` · reply: custom | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `exitPrintClean` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"inkjet_printer"}` · body: `{action:"spit_exit"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `exitPrintClean` | POST |  → `/peripheral/inkjet_printer` | ? · body: `{action:"spit_exit"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `exportLog` | GET |  → `/v1/log` | Trigger device-side log export (`M329`). · params: `{filename:s.filename}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/·/P3/HJ003/DT001 |
| `exportLog` | — |  → `/dev/log` | Trigger device-side log export (`M329`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/P2/P2S/P3/HJ003/· |
| `exportLog` | GET |  → `/file` | Trigger device-side log export (`M329`). · params: `{action:"getlog"}` | ·/·/·/·/·/·/·/·/M1/·/P1/·/·/·/·/DT001 |
| `exportLog` | GET |  → `/v1/device/log` | Trigger device-side log export (`M329`). · params: `{filename:i.filename}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `exposure` | PUT |  → `/v1/camera/exposure` | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `exposure` | POST |  → `/camera/exposure` (port 8329) | ? · params: `{stream:"0"}` | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `exposure` | POST |  → `/v1/camera` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `exposure` | GET |  → `/camera` (port 8329) | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `extenderControl` | POST |  → `/v1/extender/control` | ? | F1/·/·/GS005/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `extenderControl` | POST |  → `/extender/control` | ? | F1/·/·/GS005/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `feathering` | — |  → `KAe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `feeder` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"feeder"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `feeder` | POST |  → `/peripheral/feeder` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `feederFallBack` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"feeder"}` · body: `{action:"fallBack"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `feederFallBack` | POST |  → `/peripheral/feeder` | ? · body: `{action:"fallBack"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `feederLift` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"feeder"}` · body: `{action:"bottomBedMove",waitTime:3e4}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `feederLift` | POST |  → `/peripheral/feeder` | ? · body: `{action:"bottomBedMove",waitTime:3e4}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `feedMaterial` | POST |  → `/v1/device/feedMaterial` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `fileDownloadRequest` | PUT |  → `/v1/filetransfer/download` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/P2S/P3/HJ003/DT001 |
| `fileTransferFinish` | PUT |  → `/v1/filetransfer/finish` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/P2S/P3/HJ003/DT001 |
| `fileUploadRequest` | PUT |  → `/v1/filetransfer/upload` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/P2S/P3/HJ003/DT001 |
| `focalLen` | — |  → `d.deviceValues.mode===Se.LASER_CYLINDER?d.ext.commonResource.redCrossMeasureGif:d.ext.commonResource.autoMeasureImg` | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `focalLength` | — |  → `n.ext.commonResource.focalLengthImg` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `focalLength` | — |  → `e.ext.commonResource.focalLengthImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `focalLengthGroupNew` | — |  → `e.ext.commonResource.focalLengthImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `focalLengthMeasure` | — |  → `n.ext.commonResource.autoMeasureRedDotImg` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `focalLenMeasure` | — |  → `d.deviceValues.mode===Se.LASER_CYLINDER?d.ext.commonResource.redCrossMeasureGif:d.ext.commonResource.autoMeasureImg` | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `focalTakePicture` | — |  → `/v1/processing/focalTakePicture` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `focalTakePicture` | POST |  → `/processing/focalTakePicture` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `focusControl` | POST |  → `/v1/laser-head/focus/control` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `focusControl` | POST |  → `/focus/control` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `focusDetector` | — |  → `/v1/camera/algorithm` (port 8329) | ? · params: `{function:"smlReg"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `focusDetector` | — |  → `/algorithm` (port 8329) | ? · params: `{function:"smlReg"}` · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `gasBottle` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"airBottle"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `gasBottle` | POST |  → `/peripheral/airBottle` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `get5wLaserTemperature` | POST |  → `/peripheral/redLaserHead` | ? · body: `{action:"get_temperature"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `get5wLaserTemperature` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"redLaserHead"}` · body: `{action:"get_temperature"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getAccessoriesListViaV2Platform` | GET |  → `/v1/platform/accessories/list` | ? · params: `{}` · body: `{}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getAllDangleConnectList` | POST | `M9098` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"M9098",protocol:{type:He.F0F7,prefix:Yn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getAllDangleConnectList` | GET | `M9098` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getAllDangleConnectList` | POST | `M9098` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"M9098",protocol:{type:He.F0F7,prefix:Yn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getAllDangleConnectList` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Us}` · body: `{command:"M9098"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getAllDangleList` | POST | `M9092 T5000` → `/passthrough` (port 8080) | List nearby BLE accessories (`M9092 T<ms>`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getAllDangleList` | GET | `M9092 T5000` | List nearby BLE accessories (`M9092 T<ms>`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getAllDangleList` | POST | `M9092 T5000` → `/v1/parts/control` | List nearby BLE accessories (`M9092 T<ms>`). · body: `{link:"uart485",data_b64:lt({cmd:"M9092 T5000",protocol:{type:He.F0F7,prefix:Yn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getAllDangleList` | POST |  → `/v1/platform/accessories/control` | List nearby BLE accessories (`M9092 T<ms>`). · params: `{id:Us}` · body: `{command:"M9092 T5000"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getAttitude` | — |  → `/v1/peripheral/param` | M1 Ultra IMU attitude vector. · params: `{type:"attitude"}` · body: `{action:"get"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getAttitude` | POST |  → `/peripheral/attitude` | M1 Ultra IMU attitude vector. · body: `{action:"get"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getBackpackPurifierInfo` | POST | `M9033` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"M9033",protocol:{type:He.F0F7,prefix:kf}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getBackpackPurifierInfo` | POST | `M9033` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"M9033",protocol:{type:He.F0F7,prefix:kf}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getBatchMode` | GET |  → `/v1/processing/batch` | ? | ·/F1Ultra/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getBatchMode` | — |  → `/processing/batch` | ? · params: `{action:"query"}` | ·/F1Ultra/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getBeepEnable` | — |  → `/getBeepEnable` | Read beep state. · reply: custom | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `getBlueLaserNTC` | GET |  → `/v1/peripheral/param` | ? · params: `{type:"ntc"}` | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `getBlueLaserNTC` | GET |  → `/peripheral/ntc` | ? | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `getBlueLaserNTC` | GET |  → `/v1/laser-head/parameter` | ? · body: `{action:"blue_laser",ctrl:"get_temp"}` | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getBlueLaserNTC` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"blue_laser",ctrl:"get_temp"}` | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getCalibPicture` | — |  → `calibration/images` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getCalibrationConfig` | GET |  → `/v1/device/configs` | ? · body: `{alias:"config",type:"user",kv:["imgConfigGlobalCalib"]}` | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getCalibrationData` | — |  → `/file` | ? · params: `{action:"download",filename:"points.json"}` | ·/·/·/·/·/·/·/·/M1/·/P1/·/·/·/·/· |
| `getCameraData` | GET |  → `/file?action=download&filenameAny=/tmp/config.gz` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/P3/·/· |
| `getCameraData` | POST |  → `/v1/device/operate-log` | ? · params: `{filename:i.filename}` · body: `{action:"zipCameraData"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `getCapHeadTemp` | POST |  → `/follow/cap_head` | ? · body: `{action:"get_temp"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `getCapHeadTemp` | GET |  → `/v1/peripheral/param` | ? · params: `{type:"cap_head"}` · body: `{action:"get_temp"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `getConfig` | GET |  → `/v1/device/configs` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getConfig` | POST |  → `/config/get` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getConfigs` | GET |  → `/v1/device/configs` | ? · body: `{alias:"config",type:"user",kv:["enablePreheat","workingMode","homeAfterPowerOn","purifierTimeout","flameAlarm","gapCheck","beepEnable","taskId"]}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/·/P3/HJ003/· |
| `getConfigs` | POST |  → `/config/get` | ? · body: `{alias:"config",type:"user",kv:["enablePreheat","workingMode","homeAfterPowerOn","purifierTimeout","flameAlarm","gapCheck","beepEnable","taskId"]}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/P1/P2/P2S/P3/HJ003/· |
| `getConfigs` | GET |  → `/v1/config/get` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `getConnectedWifi` | GET |  → `/v1/net/get_connected_wifi` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getConnectedWifi` | GET |  → `/net/get_connected_wifi` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getCutPressBase` | — | `(fn)` → `cnc/cmd?cmd=M105` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `getCuttingPower` | POST |  → `/peripheral/redLaserHead` | ? · body: `{action:"get_cutoff_pwr"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getCuttingPower` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"redLaserHead"}` · body: `{action:"get_cutoff_pwr"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getDangleVersion` | POST | `M99` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:He.F0F7,prefix:Yn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getDangleVersion` | GET | `M2003` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getDangleVersion` | POST | `M99` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:He.F0F7,prefix:Yn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getDangleVersion` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Us}` · body: `{command:"M99"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getDeviceConfigs` | GET |  → `/v1/device/configs` | ? · body: `{alias:"config",type:"user",kv:["enablePreheat","workingMode","homeAfterPowerOn","purifierTimeout","flameAlarm","gapCheck","beepEnable","taskId","gapCheckWithKey"]}` | ·/·/·/GS005/·/·/·/·/·/·/·/·/·/·/·/· |
| `getDeviceConfigs` | POST |  → `/config/get` | ? · body: `{alias:"config",type:"user",kv:["enablePreheat","workingMode","homeAfterPowerOn","purifierTimeout","flameAlarm","gapCheck","beepEnable","taskId","gapCheckWithKey"]}` | ·/·/·/GS005/·/·/·/·/·/·/·/·/·/·/·/· |
| `getDeviceStatus` | GET |  → `/v1/device/runtime-infos` | Query work-state (`M222`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/·/P3/HJ003/DT001 |
| `getDeviceStatus` | — |  → `/cnc/status` | Query work-state (`M222`). | F1/·/·/GS005/·/·/·/·/·/·/·/·/·/·/·/· |
| `getDeviceStatus` | — |  → `/device/runningStatus` | Query work-state (`M222`). | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/P2/P2S/P3/HJ003/DT001 |
| `getDeviceStatus` | GET |  → `/v1/device/runningStatus` | Query work-state (`M222`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `getDeviceTasks` | GET |  → `/v1/task/list` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getDeviceTasks` | GET |  → `/task/list` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getDongleConnectStatus` | POST |  → `/device/machineInfo` (port 8080) | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getDongleConnectStatus` | GET | `M2003` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getDongleConnectStatus` | GET |  → `/v1/device/machineInfo` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getDongleConnectStatus` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Us}` · body: `{command:"M2003"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getEleBorderStatus` | — |  → `/v1/peripheral/param` | ? · params: `{type:"adsorption_mat"}` · body: `{action:"get"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getEleBorderStatus` | POST |  → `/peripheral/adsorption_mat` | ? · body: `{action:"get"}` · reply: custom | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getEmergencyStopStatus` | GET |  → `/v1/peripheral/param` | ? · params: `{type:"emergency_stop"}` | ·/F1Ultra/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `getEmergencyStopStatus` | GET |  → `/peripheral/emergency_stop` | ? | ·/F1Ultra/·/·/·/GS004-CLASS-4/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `getError` | GET |  → `/v1/device/alarms` | ? | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getError` | — |  → `/alarm/getRecord` | ? | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getFanBootVersion` | POST | `M99` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"",protocol:{type:He.F0F7,prefix:[70,97,17]}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanBootVersion` | GET | `boot` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanBootVersion` | POST | `M99` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"",protocol:{type:He.F0F7,prefix:[70,97,17]}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanBootVersion` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:su}` · body: `{command:"M99"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanInfo` | POST | `M9082` → `/passthrough` (port 8080) | Duct-fan diagnostic snapshot (`M9082`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanInfo` | GET | `M9082` | Duct-fan diagnostic snapshot (`M9082`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanInfo` | POST | `M9082` → `/v1/parts/control` | Duct-fan diagnostic snapshot (`M9082`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanInfo` | POST |  → `/v1/platform/accessories/control` | Duct-fan diagnostic snapshot (`M9082`). · params: `{id:su}` · body: `{command:"M9082"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanInfoV3` | POST | `M9082` → `/passthrough` (port 8080) | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanInfoV3` | GET | `M9082` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanInfoV3` | POST | `M9082` → `/v1/parts/control` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanInfoV3` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Za}` · body: `{command:"M9082"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanV3BootVersion` | POST | `(fn)` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"",protocol:{type:He.F0F7,prefix:[78,97,17]}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanV3BootVersion` | GET | `boot` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanV3BootVersion` | POST | `(fn)` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"",protocol:{type:He.F0F7,prefix:[78,97,17]}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanVersion` | GET | ` ` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanVersion` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:su}` · body: `{command:"M99"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanVersionV3` | POST | `M99` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:He.F0F7,prefix:yn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanVersionV3` | GET | `M99` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanVersionV3` | POST | `M99` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:He.F0F7,prefix:yn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFanVersionV3` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Za}` · body: `{command:"M99"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getFireMsg` | POST |  → `/dev/operateRecord` (port 8080) | ? · body: `{action:"getFireMsg"}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `getFireMsg` | POST |  → `/v1/device/operate-log` | ? · params: `{filename:i.filename}` · body: `{action:"getFireMsg"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `getFireMsg` | POST |  → `/v1/device/asyncProcess` | ? · params: `{action:"firelog"}` · body: `{action:"get"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getFireMsg` | POST |  → `/dev/firelog` (port 8080) | ? · body: `{action:"get"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getFirePhoto` | POST |  → `/dev/operateRecord` (port 8080) | ? · body: `{action:"getBackgroundImage"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `getFireTar` | POST |  → `/dev/operateRecord` (port 8080) | ? · body: `{action:"getFireImage"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `getFirmwareVersion` | — |  → `/system` | Firmware version (model-specific source). · params: `{action:"version_v2"}` · reply: custom | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `getFlamePicture` | GET |  → `/v1/camera/fire-record` | ? · params: `{filename:r.path}` | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getFlamePicture` | POST |  → `/camera/fireRecord` (port 8329) | ? | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getFocusInfo` | GET |  → `/v1/laser-head/focus/parameter` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getFocusInfo` | POST |  → `/focus/control` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `getGlobalCalibrationData` | — |  → `/file` | ? · params: `{action:"download",filename:"global_calib.json"}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/HJ003/· |
| `getGlobalCalibrationData` | GET |  → `/v1/file/content` | ? · params: `{type:"global_calib.json"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `getGlobalCamData` | — |  → `/camera/flash` (port 8329) | ? · params: `{stream:"0"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `getGlobalCamData` | GET |  → `/v1/camera/flash` (port 8329) | ? · params: `{stream:"0",fmt:"text"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `getGlobalIrData` | — |  → `/file` | ? · params: `{action:"download",filename:"global_ir.txt"}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `getGlobalIrData` | GET |  → `/v1/file/content` | ? · params: `{type:"global_ir.txt"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `getGluePowder` | GET |  → `/peripheral/glue_powder` | DT001 glue/powder cartridge. | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getHandheldGear1Power` | POST |  → `/v1/project/device/accessory/control` | ? · params: `{level:1}` · body: `{name:rl,command:"get_power",params:{level:1}}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getHandheldGear2Power` | POST |  → `/v1/project/device/accessory/control` | ? · params: `{level:2}` · body: `{name:rl,command:"get_power",params:{level:2}}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getHeadPosition` | GET |  → `/v1/peripheral/param` | ? · params: `{type:"position"}` · body: `{aix:"all",datatype:"absolute"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getHeadPosition` | POST |  → `/peripheral/position` | ? · body: `{aix:"all",datatype:"absolute"}` · reply: custom | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getHeadPosition` | GET |  → `/v1/motion/position` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getHeadPosition` | GET |  → `/motion/position` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getHeaterTemp` | GET |  → `/v1/peripheral/heater_temp` | DT001 heater temperature. | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getHeaterTemp` | GET |  → `/peripheral/heater_temp` | DT001 heater temperature. | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getHistoryTasks` | GET |  → `/v1/task/history` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getHistoryTasks` | GET |  → `/task/history` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getInkBottle` | GET |  → `/v1/peripheral/ink_bottle` | DT001 ink bottle level. | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getInkBottle` | POST |  → `/peripheral/ink_bottle` | DT001 ink bottle level. | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getInkStationStatus` | GET |  → `/v1/peripheral/ink_station` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getInkStationStatus` | GET |  → `/peripheral/ink_station` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getIrConfig` | GET |  → `/v1/device/configs` | ? · body: `{alias:"config",type:"user",kv:["imgConfigGlobalIr"]}` | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getKnifeHeadType` | — |  → `/v1/peripheral/param` | ? · params: `{type:"knife_head"}` · body: `{action:"get_sync"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getKnifeHeadType` | POST |  → `/peripheral/knife_head` | ? · body: `{action:"get_sync"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getLaserPosition` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"get_coord",waitTime:0}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/P3/HJ003/· |
| `getLaserPosition` | POST |  → `/v1/laser-head/control` | ? · body: `{action:"get_coord",waitTime:0}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `getLaserPosition` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"laser_head"}` · body: `{action:"get_coord",waitTime:3e3}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/HJ003/· |
| `getLaserReady` | POST |  → `/v1/laser-head/parameter` | ? · body: `{action:"laser_ready"}` | ·/F1Ultra/·/·/·/GS004-CLASS-4/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `getLaserReady` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"laser_ready"}` | ·/F1Ultra/·/·/·/GS004-CLASS-4/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `getLensesType` | GET |  → `/v1/peripheral/param` | ? · params: `{type:"cj_check"}` · body: `{action:"get"}` | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getLensesType` | POST |  → `/peripheral/cj_check` | ? · body: `{action:"get"}` | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getLocalCalibrationData` | — |  → `/file` | ? · params: `{action:"download",filename:"local_calib.json"}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/HJ003/· |
| `getLocalCalibrationData` | GET |  → `/v1/file/content` | ? · params: `{type:"local_calib.json"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `getLocalCamData` | — |  → `/camera/flash` (port 8329) | ? · params: `{stream:"1"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `getLocalCamData` | GET |  → `/v1/camera/flash` (port 8329) | ? · params: `{stream:"1",fmt:"text"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `getLocalIrData` | — |  → `/file` | ? · params: `{action:"download",filename:"local_ir.txt"}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `getLocalIrData` | GET |  → `/v1/file/content` | ? · params: `{type:"local_ir.txt"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `getMachineInfo` | GET |  → `/v1/device/machineInfo` | Read /device/machineInfo JSON. | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getMachineInfo` | GET |  → `/device/machineInfo` | Read /device/machineInfo JSON. | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getMachineInformation` | POST |  → `/v1/device/machine_information` | ? · body: `{kv:["offlineProtoModeEnable","offlineProtoProModeEnable","offlineOptionModeEnable","flameAlarm","flameSensitivity","fillLightBrightness","purifierTimeout","motionMinPowerFill","motionMinPowerCarve","motionMinPowerCutting","laserMeasureOffset","purifierEnable","beepEnable","taskId","smokeFanSpeed","feeder","autoCheckStatus","enableUserCheck","focusPlaneDistanceDiff","feederVdistanceOffset","first_setting_msg","flameDetectLevelHLSelect","VMeasurePoint","vAxisHomeFlag"]}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getMachiningPower` | POST |  → `/v1/project/device/accessory/control` | ? · params: `{}` · body: `{name:rl,command:"get_machine_power",params:{}}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getMotionDelayTimeData` | GET |  → `/v1/motion_control/paramter` | ? · params: `PA` · body: `{action:"infoGet",data:[{type:"red",params:PA},{type:"blue",params:PA}]}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getMotionDelayTimeData` | POST |  → `/peripheral/motion_control` | ? · params: `SA` · body: `{action:"infoGet",data:[{type:"red",params:SA},{type:"blue",params:SA}]}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getMultiFunctionalBaseInfo` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Vv}` · body: `{command:"M9033"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getNonBleAccessories` | POST |  → `/device/machineInfo` (port 8080) | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getNonBleAccessories` | GET | `M2003` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getNonBleAccessories` | GET |  → `/v1/device/machineInfo` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getNonBleAccessoryFirmwareInfo` | POST |  → `/device/machineInfo` (port 8080) | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getNonBleAccessoryFirmwareInfo` | GET | `M2003` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getNonBleAccessoryFirmwareInfo` | GET |  → `/v1/device/machineInfo` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getOffset` | — |  → `/v1/peripheral/param` | ? · params: `{type:"crossred_Offset"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getOffset` | POST |  → `/peripheral/crossred_Offset` | ? · reply: custom | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getOperateLog` | GET |  → `/v1/device/operate-log` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getPeripheralParameter` | GET |  → `/v1/peripheral/param` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getPowerResume` | GET |  → `/v1/processing/powerResume` | ? · params: `{action:"query"}` | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getPowerResume` | — |  → `/processing/powerResume` | ? · params: `{action:"query"}` | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getPrinterAllInfo` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"get_ink_status"}` · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getPrinterBaseInfo` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"get_info"}` · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getPrinterCapStatus` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"get_cap_status"}` · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getPrinterInfo` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"get_info"}` · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getPrinterInkStatus` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"get_ink_status"}` · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getPrinterOffsetByLaser` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"get_touch_shift"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getPrinterRedCrossOffset` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"get_crossred_shift"}` · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getPrinterTonerStatus` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"get_toner_install"}` · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getPrinterTouchShift` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"get_touch_shift"}` · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getPrintHead` | — |  → `/v1/peripheral/param` | ? · params: `{type:"inkjet_printer"}` · body: `{action:"get"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getPrintHead` | POST |  → `/peripheral/inkjet_printer` | ? · body: `{action:"get"}` · reply: custom | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getProcessHeadType` | — |  → `/v1/peripheral/param` | ? · params: `{type:"workhead_ID"}` · body: `{action:"get"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getProcessHeadType` | POST |  → `/peripheral/workhead_ID` | ? · body: `{action:"get"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getProcessingTime` | GET |  → `/v1/processing/worktime` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getPurifierBootVersion` | POST | `M99` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"",protocol:{type:He.F0F7,prefix:RN}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierBootVersion` | GET | `boot` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierBootVersion` | POST | `M99` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"",protocol:{type:He.F0F7,prefix:RN}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierBootVersion` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:rg}` · body: `{command:"boot"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierInfo` | POST | `M9033` → `/passthrough` (port 8080) | Purifier V3 status snapshot (`M9033`, BLE F0F7 framing). · body: `{link:"uart485",data_b64:lt({cmd:"M9033",protocol:{type:He.F0F7,prefix:ec}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierInfo` | GET | `M9033` | Purifier V3 status snapshot (`M9033`, BLE F0F7 framing). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierInfo` | POST | `M9033` → `/v1/parts/control` | Purifier V3 status snapshot (`M9033`, BLE F0F7 framing). · body: `{link:"uart485",data_b64:lt({cmd:"M9033",protocol:{type:He.F0F7,prefix:ec}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierInfo` | POST |  → `/v1/platform/accessories/control` | Purifier V3 status snapshot (`M9033`, BLE F0F7 framing). · params: `{id:rg}` · body: `{command:"M9033"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierInfoV3` | POST | `M9033` → `/passthrough` (port 8080) | Purifier V3 status (`M9033`). · body: `{link:"uart485",data_b64:lt({cmd:"M9033",protocol:{type:He.F0F7,prefix:Zn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierInfoV3` | GET | `M9033` | Purifier V3 status (`M9033`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierInfoV3` | POST | `M9033` → `/v1/parts/control` | Purifier V3 status (`M9033`). · body: `{link:"uart485",data_b64:lt({cmd:"M9033",protocol:{type:He.F0F7,prefix:Zn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierInfoV3` | POST |  → `/v1/platform/accessories/control` | Purifier V3 status (`M9033`). · params: `{id:nl}` · body: `{command:"M9033"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierV3BootVersion` | POST | `M99` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:He.F0F7,prefix:Zn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierV3BootVersion` | GET | `M99` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierV3BootVersion` | POST | `M99` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:He.F0F7,prefix:Zn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierV3BootVersion` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:nl}` · body: `{command:"M99"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierV3RCVersion` | POST | `M9032` → `/passthrough` (port 8080) | Purifier V3 RC firmware version (`M9032`). · body: `{link:"uart485",data_b64:lt({cmd:"M9032",protocol:{type:He.F0F7,prefix:Zn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierV3RCVersion` | GET | `M9032` | Purifier V3 RC firmware version (`M9032`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierV3RCVersion` | POST | `M9032` → `/v1/parts/control` | Purifier V3 RC firmware version (`M9032`). · body: `{link:"uart485",data_b64:lt({cmd:"M9032",protocol:{type:He.F0F7,prefix:Zn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierV3RCVersion` | POST |  → `/v1/platform/accessories/control` | Purifier V3 RC firmware version (`M9032`). · params: `{id:nl}` · body: `{command:"M9032"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierV3Version` | POST | `M99` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:He.F0F7,prefix:Zn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getPurifierV3Version` | POST | `M99` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"M99",protocol:{type:He.F0F7,prefix:Zn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getRa3AccessoryType` | — |  → `/v1/camera/algorithm` (port 8329) | ? · params: `{function:"ra3AttaType"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getRa3AccessoryType` | GET |  → `/algorithm?function=ra3AttaType` (port 8329) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getRa3KeyPoint` | — |  → `/v1/camera/algorithm` (port 8329) | ? · params: `{function:"ra3KeyPoint"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getRa3KeyPoint` | GET |  → `/algorithm?function=ra3KeyPoint` (port 8329) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getRecordStatus` | GET |  → `v1/recordctrl` (port 8089) | ? · params: `{stream:2,action:"status"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getRedCrossOffset` | POST |  → `/peripheral/redLaserHead` | ? · body: `{action:"get_ten_word_offset"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getRedCrossOffset` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"redLaserHead"}` · body: `{action:"get_ten_word_offset"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getRedDotStatus` | POST |  → `/weld` | ? · body: `{type:"get_ir_power"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `getRedDotStatus` | GET |  → `/v1/weld` | ? · body: `{type:"get_ir_power"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `getRedLaserConfig` | POST | `M99` → `/peripheral/redLaserHead` | ? · params: `{cmd:"M99",dest:46,wait:!0,force:1}` · body: `{action:"get_SN"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getRedLaserConfig` | PUT | `M99` → `/v1/peripheral/param` | ? · params: `{type:"redLaserHead"}` · body: `{action:"get_SN"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getRedLaserFocalOffset` | POST |  → `/peripheral/redLaserHead` | ? · body: `{action:"get_focus_z_offset"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getRedLaserFocalOffset` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"redLaserHead"}` · body: `{action:"get_focus_z_offset"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getRedLaserOffset` | POST |  → `/peripheral/redLaserHead` | ? · body: `{action:"get_location_offset"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getRedLaserOffset` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"redLaserHead"}` · body: `{action:"get_location_offset"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getResolution` | GET |  → `/v1/get-res` (port 8329) | ? · params: `{camera:"all"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getResolution` | — |  → `/v1/camera/params/get_res` (port 8329) | ? · params: `{camera:"all"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getResolution` | GET |  → `/get-res` (port 8329) | ? · params: `{camera:"all"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getRoaster` | GET |  → `/v1/device/roaster` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getRoaster` | GET |  → `/device/roaster` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getSafetyFireBoxProInfo` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Fie}` · body: `{command:"M9033"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getSDMemoryUsage` | GET |  → `/v1/sdcardSpace ` (port 8089) | ? · reply: custom | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getSnCode` | GET |  → `/v1/peripheral/param` | ? · params: `{type:"crossred_Offset"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getSnCode` | POST |  → `/peripheral/crossred_Offset` | ? · reply: custom | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getSsid` | GET |  → `/v1/wifi/connected-ssid` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/P3/·/· |
| `getStatus` | GET |  → `/v1/device/runtime-infos` | ? | F1/·/·/GS005/GS006/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getStatus` | — |  → `/cnc/status` | ? · reply: custom | F1/·/·/GS005/·/·/·/·/M1/·/·/·/·/·/·/· |
| `getStatus` | — |  → `/device/runningStatus` | ? · reply: custom | ·/·/·/·/GS006/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getSubInfo` | GET |  → `/device/subHeadInfo` | ? · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getTaskId` | — |  → `/cnc/status` | ? · reply: custom | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `getTaskId` | GET |  → `/v1/device/configs` | ? · body: `{alias:"config",type:"user",kv:["taskId"]}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getTaskId` | POST |  → `/config/get` | ? · body: `{alias:"config",type:"user",kv:["taskId"]}` · reply: custom | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getTempAndhumidity` | GET |  → `/v1/peripheral/temp_humidity` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getTempAndhumidity` | POST |  → `/peripheral/temp_humidity` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getTemperature` | GET |  → `/v1/peripheral/param` | ? · params: `{type:"Z_ntc_temp"}` · body: `{action:"get"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getTemperature` | POST |  → `/peripheral/Z_ntc_temp` | ? · body: `{action:"get"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getUltrasonicKnifeControlMode` | POST |  → `/v1/project/device/accessory/control` | ? · params: `{}` · body: `{name:rl,command:"get_control_mode",params:{}}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `getUsbLockStatus` | GET |  → `/v1/peripheral/param` | ? · params: `{type:"machine_lock"}` | ·/F1Ultra/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getUsbLockStatus` | — |  → `/peripheral/machine_lock` | ? | ·/F1Ultra/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `getValueZ` | GET |  → `/v1/laser-head/parameter` | ? · body: `{action:"get_coord"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getValueZ` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"get_coord"}` · reply: custom | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `getVideoList` | GET |  → `/v1/medialist` (port 8089) | ? · params: `{stream:2}` · body: `a` · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getWaterBottle` | GET |  → `/v1/peripheral/water_bottle` | DT001 water tank. | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getWaterBottle` | POST |  → `/peripheral/water_bottle` | DT001 water tank. | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getWeldFocus` | POST |  → `/weld` | ? · body: `{type:"get_weld_focus"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `getWeldFocus` | GET |  → `/v1/weld` | ? · body: `{type:"get_weld_focus"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `getWifiConfig` | GET |  → `/v1/wifi/credentials` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `getWifiSignalStrength` | POST |  → `/v1/net/wifi_signal_strength` | ? · body: `{name:"wlan0"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/P3/·/· |
| `getWifiSignalStrength` | GET |  → `/v1/net/get_wifi_signal_strength` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getWifiSignalStrength` | POST |  → `/net/get_wifi_signal_strength` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getWorkingInfo` | GET |  → `/v1/device/workingInfo` | Read /device/workingInfo. | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getWorkingInfo` | GET |  → `/device/workingInfo` | Read /device/workingInfo. | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `getWorkingTime` | — |  → `/device/workingInfo` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/HJ003/· |
| `getWorkingTime` | GET |  → `/v1/device/workingInfo` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `getWorkingTime` | GET |  → `/v1/device/statistics` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/HJ003/· |
| `getWorkMsg` | POST |  → `/dev/operateRecord` | ? · body: `{action:"getWorkMsg"}` · reply: custom | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/P3/·/· |
| `getWorkMsg` | POST |  → `/v1/device/operate-log` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `handMoveCenterPoint` | GET |  → `/v1/camera/algorithm` (port 8329) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `handMoveCenterPoint` | POST |  → `/algorithm` (port 8329) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `hardness` | — |  → `YPt` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `hasCapHead` | POST |  → `/follow/cap_head` | ? · body: `{action:"is_on"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `hasCapHead` | GET |  → `/v1/peripheral/param` | ? · params: `{type:"cap_head"}` · body: `{action:"is_on"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `heightMeasureMode` | — |  → `Nt.heightMode` | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `ignoreFireLevel1` | POST |  → `/dev/operateRecord` (port 8080) | ? · body: `{action:"setUserConfirmFire"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `ignoreFireLevel1` | POST |  → `/v1/device/operate-log` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `imageCorrection` | — |  → `Wr.cylinderImageCorrectionSwitchImg` | ? | ·/F1Ultra/·/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `imageCorrection` | — |  → `La.cylinderImageCorrectionSwitchImg` | ? | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `imageCorrection` | — |  → `ja.cylinderImageCorrectionSwitchImg` | ? | ·/·/·/·/·/GS004-CLASS-4/·/·/·/·/·/·/·/·/·/· |
| `imageCorrection` | — |  → `Fa.cylinderImageCorrectionSwitchImg` | ? | ·/·/·/·/·/·/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `imageCorrection` | — |  → `Wa.cylinderImageCorrectionSwitchImg` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `imageCorrection` | — |  → `Dr.correctionSwitchImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/·/·/·/· |
| `imageCorrection` | — |  → `Dr.cylinderImageCorrectionSwitchImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/·/·/·/· |
| `imageCorrection` | — |  → `On.correctionSwitchImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `imageCorrection` | — |  → `On.cylinderImageCorrectionSwitchImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `imageCorrection` | — |  → `ni.correctionSwitchImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `imageCorrection` | — |  → `ni.cylinderImageCorrectionSwitchImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `imagePrinting` | — |  → `rCe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `inkExtraction` | POST |  → `/v1/peripheral/ink_extraction` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `inkExtraction` | POST |  → `/peripheral/ink_extraction` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `intoBedLeveling` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"bottomBed"}` · body: `{action:"leveling"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `intoBedLeveling` | POST |  → `/peripheral/bottomBed` | ? · body: `{action:"leveling"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `ip` | GET |  → `/v1/wifi/interfaces` | Read device IP (`M2002` or /net/ifconfig). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/P2S/P3/HJ003/· |
| `ip` | — |  → `/net/ifconfig` | Read device IP (`M2002` or /net/ifconfig). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/P2S/P3/HJ003/DT001 |
| `ip` | — |  → `/net` | Read device IP (`M2002` or /net/ifconfig). · params: `{action:"ifconfig",t:Date.now()}` | ·/·/·/·/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/· |
| `ircut` | GET |  → `/v1/ircut` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `ircut` | — |  → `/ircut` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `ircutControl` | GET |  → `camera` | ? | ·/F1Ultra/·/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `irMeasureDistance` | POST |  → `/peripheral/ir_measure_distance` (port 8080) | ? · body: `{action:"get_distance",type:"single"}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/P3/·/· |
| `irMeasureDistance` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"ir_measure_distance"}` · body: `{action:"get_distance",type:"single"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `isAllowDownloadFirmware` | — |  → `/processing/print_type` | ? · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `isAllowDownloadFirmware` | GET |  → `/v1/processing/type` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `isCalibration` | — |  → `follow/is_calibration` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `isCalibration` | GET |  → `/v1/peripheral/param` | ? · params: `{type:"is_calibration"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `isCover` | GET |  → `/v1/peripheral/param` | ? · params: `{type:"gap"}` · body: `{action:"get"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/P2S/P3/HJ003/· |
| `isCover` | POST |  → `/peripheral/gap` | ? · body: `{action:"get"}` · reply: custom | ·/·/·/·/·/·/·/·/·/M1Ultra/·/P2/P2S/P3/HJ003/· |
| `isDeviceBusy` | — |  → `/cnc/status` | ? · reply: custom | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `isHomingZ` | — |  → `/v1/laser-head/parameter` | ? · body: `{action:"is_homeing"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `isHomingZ` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"is_homeing"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `isIdle` | — |  → `/cnc/status` | ? · reply: JSON | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `isIdle` | — |  → `/processing/print_type` | ? · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/P3/HJ003/· |
| `isIdle` | GET |  → `/v1/processing/type` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/HJ003/· |
| `isMeasuring` | — |  → `/processing/print_type` | ? · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/P3/·/· |
| `isMeasuring` | GET |  → `/v1/processing/type` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `isNotPowerOn` | POST |  → `/device/readyStatus` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `isPrintTexture` | — |  → `DUe` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `isProcessByLayer` | — |  → `PIe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `isResetInterrupt` | — |  → `/v1/laser-head/parameter` | ? · body: `{action:"getResetInterrupt"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `isResetInterrupt` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"getResetInterrupt"}` · reply: custom | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `isWideCleaning` | POST |  → `/weld` | ? · body: `{type:"get_wide_cleaning_flag"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `isWideCleaning` | GET |  → `/v1/weld` | ? · body: `{type:"get_wide_cleaning_flag"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `laserHeadBusyState` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"is_busy"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `laserHeadBusyState` | GET |  → `/v1/peripheral/param` | ? · params: `{type:"laser_head"}` · body: `{action:"is_busy"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `laserHeadCalibration` | POST |  → `/follow/calibration` (port 8080) | ? · body: `{action:"start"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `laserHeadCalibration` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"calibration"}` · body: `{action:"start"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `laserHeadCalibration` | — |  → `wNe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `laserHeadIsHoming` | — |  → `/peripherystatus` | ? · params: `{action:"laserhead"}` · reply: custom | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `laserHeadPreciseMeasurement` | — |  → `W_t` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/·/·/·/· |
| `laserHeadPreciseMeasurement` | — |  → `Yyt` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `limitTrigger` | — |  → `/v1/peripheral/param` (port 8080) | ? · params: `{type:"doorStatus"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `limitTrigger` | POST |  → `/peripheral/doorStatus` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `listWifi` | GET |  → `/v1/wifi/ap-list` | Scan available SSIDs (`M2000` or `/net/get_ap_list`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/P2S/P3/HJ003/· |
| `listWifi` | — |  → `/net/get_ap_list` | Scan available SSIDs (`M2000` or `/net/get_ap_list`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/P2/P2S/P3/HJ003/DT001 |
| `listWifi` | — |  → `/net` | Scan available SSIDs (`M2000` or `/net/get_ap_list`). · params: `{action:"aplist"}` · reply: custom | ·/·/·/·/·/·/·/·/M1/·/P1/·/·/·/·/· |
| `lockCover` | POST |  → `/peripheral/digital_lock` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/P3/·/· |
| `lockCover` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"digital_lock"}` · body: `{action:"on"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `lockedLeaserHead` | — | `(fn)` → `/cnc/cmd?cmd=M110 S15` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `lockShaft` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"lock_motor"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `lockShaft` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"laser_head"}` · body: `{action:"lock_motor"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `logTar` | PUT |  → `/v1/log/tar` | ? | ·/·/GS003/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `mdStartPrint` | — |  → `/processing/start` | ? | ·/F1Ultra/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/·/P3/·/· |
| `mdStartPrint` | PUT |  → `/v1/processing/state` | ? · params: `{action:"start"}` | ·/F1Ultra/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/·/·/·/· |
| `measure` | GET |  → `/v1/peripheral/param` | ? · params: `{type:"workhead_ZHeight"}` · body: `{action:"get"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `measure` | POST |  → `/peripheral/workhead_ZHeight` | ? · body: `{action:"get"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `measurePrinterZ` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"get_z_axis_distance"}` · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `measureThick` | — |  → `camera` (port 8329) | ? · params: `{}` · reply: custom | ·/·/·/·/·/·/·/·/M1/·/P1/·/·/·/·/· |
| `mesure` | — |  → `e.ext.commonResource.cylCurveImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `modeSwitch` | PUT |  → `/v1/device/mode` | ? | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/P2S/P3/·/· |
| `modeSwitch` | POST |  → `/device/modeSwitch` (port 8080) | ? | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/P2S/P3/·/· |
| `motionAlwaysGo` | POST |  → `/v1/motion/always_go` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `motionAlwaysGo` | POST |  → `/motion/always_go` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `motionDelayTime` | PUT |  → `/v1/motion_control/paramter` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `motionDelayTime` | POST |  → `/peripheral/motion_control` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `motionGo` | POST |  → `/v1/motion/go` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `motionGo` | POST |  → `/motion/go` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `motionGoTo` | POST |  → `/v1/motion/go_to` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `motionGoTo` | POST |  → `/motion/go_to` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `motionStop` | POST |  → `/v1/motion/stop` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `motionStop` | POST |  → `/motion/stop` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `moveBelt` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"conveyor"}` | ·/F1Ultra/·/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `moveBelt` | POST |  → `peripheral/conveyor` | ? | ·/F1Ultra/·/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `moveHead` | PUT |  → `/v1/laser-head/parameter` | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `moveHead` | POST |  → `/peripheral/laser_head` | ? · reply: custom | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `movePrinterZ` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"set_z_axis_specified_position",distance:0}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `moveToTouch` | PUT |  → `/v1/laser-head/parameter` | ? · body: `{action:"go_to_bltouch",waitTime:5e3}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `moveToTouch` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"go_to_bltouch",waitTime:5e3}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `multiPoint` | — |  → `r.ext.commonResource.multiPointImg` | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `nearCalibFile` | GET |  → `/v1/camera/algorithm` (port 8329) | ? · params: `{function:"nearFile"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `nearCalibFile` | — |  → `/algorithm` (port 8329) | ? · params: `{function:"nearFile"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `notifyFirmwareUpgrade` | PUT |  → `/v1/device/upgrade-mode` | ? · params: `{mode:"upgrade"}` · body: `{force_upgrade:1,action:"burn",atomm:1}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/P2S/P3/HJ003/DT001 |
| `notifyPdInfo` | POST |  → `/v1/task/upload` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `offLight` | POST |  → `/peripheral/fill_light` | ? · body: `{action:"set_bri",idx:0,value:0}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/P3/·/· |
| `offLight` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"fill_light"}` · body: `{action:"set_bri",idx:0,value:0}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `openNozzleCheck` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"test_for_print_demo",debug:!0}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `openPrinterRedCross` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"set_red_light",status:"on"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `openRecord` | — |  → `requstream` (port 8329) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `panoramaButton` | — |  → `NPt` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `paperDetection` | POST |  → `/v1/device/selfTest` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `paperDetection` | POST |  → `/device/selfTest` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `pausePrint` | PUT |  → `/v1/processing/state` | ? · params: `{action:"pause"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/P2S/P3/HJ003/· |
| `pausePrint` | GET |  → `/processing/pause` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/P2/P2S/P3/HJ003/DT001 |
| `pausePrint` | — |  → `/cnc/data?action=pause` | ? | ·/·/·/·/·/·/·/·/M1/·/P1/·/·/·/·/· |
| `pausePrint` | POST |  → `/v1/processing/pause` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `peripheralKnife` | POST |  → `/v1/peripheral/knife` | ? · params: `{action:"update"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `peripheralKnife` | POST |  → `/peripheral/knife` | ? · params: `{action:"update"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `powderDischargeAuto` | POST |  → `/v1/powderDischarge/auto_process` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `powderDischargeAuto` | POST |  → `/powderDischarge/auto_process` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `powderDischargeFinish` | POST |  → `/v1/powderDischarge/finish` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `powderDischargeFinish` | POST |  → `/powderDischarge/finish` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `powderDischargeStart` | POST |  → `/v1/powderDischarge/start` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `powderDischargeStart` | POST |  → `/powderDischarge/start` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `powderLoop` | POST |  → `/v1/peripheral/powder_loop` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `powderLoop` | POST |  → `/peripheral/powder_loop` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `powerResume` | — |  → `/processing/powerResume` | ? | ·/·/GS003/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `preciseMeasurementBtn` | — |  → `e.deviceValues.mode===Ee.LASER_PLANE?he.commonResource.aiMeasureThicknessImg:he.commonResource.aiMeasureFocalLengthImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/·/·/·/· |
| `preciseMeasurementBtn` | — |  → `e.deviceValues.mode===ye.LASER_PLANE?U.commonResource.aiMeasureThicknessImg:U.commonResource.aiMeasureFocalLengthImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `preciseMeasurementBtn` | — |  → `T` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `preciseMeasurementBtn` | — |  → `iZe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `printCalibImage` | POST |  → `/v1/calibration/print` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `printCalibImage` | POST |  → `/calibration/print` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `processing` | GET |  → `/v1/processing/progress` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/P2S/P3/HJ003/· |
| `processing` | — |  → `/processing/progress` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/P2/P2S/P3/HJ003/· |
| `processingKnifeReset` | POST |  → `/v1/processing/knifeReset` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `processingKnifeReset` | POST |  → `/processing/knifeReset` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `processingMode` | — |  → `/processing/print_type` | ? · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/P3/·/· |
| `processingMode` | GET |  → `/v1/processing/type` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `queryCurrentTaskId` | POST |  → `/config/get` | ? · body: `{alias:"config",type:"user",kv:["taskId"]}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `queryCurrentTaskId` | GET |  → `/v1/config/get` | ? · body: `{alias:"config",type:"user",kv:["taskId"]}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `queryCurrentTaskId` | GET |  → `/v1/device/configs` | ? · body: `{alias:"config",type:"user",kv:["taskId"]}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `queryProcessPercent` | POST |  → `/cnc/data?query=progress&random=' + ${Math.random()*1e3}` | ? · reply: custom | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `queryRunningStatus` | GET |  → `/v1/device/runtime-infos` | ? · reply: JSON | F1/F1Ultra/·/GS005/GS006/·/·/·/·/·/·/·/·/P3/HJ003/· |
| `queryRunningStatus` | — |  → `/device/runningStatus` | ? · reply: JSON | F1/F1Ultra/GS003/GS005/GS006/·/·/·/·/·/·/P2/P2S/P3/HJ003/· |
| `queryRunningStatus` | GET |  → `/v1/device/runningStatus` | ? · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `quickMeasurementBtn` | — |  → `he.commonResource.autoMeasureRedDotImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/·/·/·/· |
| `quickMeasurementBtn` | — |  → `U.commonResource.autoMeasureRedDotImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `raiseButton` | — |  → `Wr.ra3RaiseAngleTips2` | ? | ·/F1Ultra/·/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `raiseButton` | — |  → `La.ra3RaiseAngleTips2` | ? | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `raiseButton` | — |  → `ja.ra3RaiseAngleTips2` | ? | ·/·/·/·/·/GS004-CLASS-4/·/·/·/·/·/·/·/·/·/· |
| `raiseButton` | — |  → `Fa.ra3RaiseAngleTips2` | ? | ·/·/·/·/·/·/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `raiseButton` | — |  → `Wa.ra3RaiseAngleTips2` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `raiseButton` | — |  → `Dr.ra3RaiseAngleTips2` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/·/·/·/· |
| `raiseButton` | — |  → `On.ra3RaiseAngleTips2` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `raiseButton` | — |  → `ni.ra3RaiseAngleTips2` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `recoveryCamCali` | — |  → `recoveryCamCali` | ? · reply: custom | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `recycleInk` | POST |  → `/v1/peripheral/water_out_pump` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `recycleInk` | POST |  → `/peripheral/water_out_pump` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `redDotCalibration` | POST |  → `weld` | ? · body: `{type:"set_ir_offset"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `redDotCalibration` | PUT |  → `/v1/weld` | ? · body: `{type:"set_ir_offset"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `removeUpdateTag` | — |  → `/removeupgradetag` | REST: clear post-upgrade flag. | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `replaceWalkBorder` | PUT |  → `/v1/processing/frame/replace` | ? · params: `{fileType:1,fileName:"tmpFrameNew.gcode"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `replaceWalkBorder` | POST |  → `/processing/replace` | ? · params: `{}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `resetCutPressBase` | — |  → `recoveryMotionoffsiteCali?type=V` | ? · reply: custom | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `resetFilterWorkTime` | POST |  → `/v1/platform/accessories/control` | Reset purifier filter timer (`M9258`). · params: `{id:Vv}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `resetFilterWorkTime` | POST | `M9258 ${e.data.filterType}0` → `/passthrough` (port 8080) | Reset purifier filter timer (`M9258`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `resetFilterWorkTime` | POST | `M9258 ${e.filterType}0` → `/v1/parts/control` | Reset purifier filter timer (`M9258`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `resetFilterWorkTime` | POST | `M9258 ${t.data.filterType}0` → `/passthrough` (port 8080) | Reset purifier filter timer (`M9258`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `resetFilterWorkTime` | POST | `M9258 ${t.filterType}0` → `/v1/parts/control` | Reset purifier filter timer (`M9258`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `resetFilterWorkTime` | POST | `M9258 ${n.data.filterType}0` → `/passthrough` (port 8080) | Reset purifier filter timer (`M9258`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `resetFilterWorkTime` | POST | `M9258 ${n.filterType}0` → `/v1/parts/control` | Reset purifier filter timer (`M9258`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `resetFilterWorkTime` | POST | `M9258 ${r.data.filterType}0` → `/passthrough` (port 8080) | Reset purifier filter timer (`M9258`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `resetFilterWorkTime` | POST | `M9258 ${r.filterType}0` → `/v1/parts/control` | Reset purifier filter timer (`M9258`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `resetFireRecord` | POST |  → `/v1/camera/fire-record/clear` | ? | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `resetFireRecord` | POST |  → `camera/resetFireRecord` (port 8329) | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `resetFireRecord` | GET |  → `/camera/resetFireRecord` (port 8329) | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `resetLaserHead` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"go_to",waitTime:0,x:0,y:0}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/P3/·/· |
| `resetLaserHead` | POST |  → `/v1/laser-head/control` | ? · body: `{action:"go_to",waitTime:0,f:uf,x:0,y:0}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `resetLaserHead` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"laser_head"}` · body: `{action:"go_to",waitTime:3e4,f:La,x:0,y:0}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `resetLeaserHead` | — | `(fn)` → `/cnc/cmd?cmd=$H` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `resetOrigin` | PUT |  → `/v1/laser-head/parameter` | ? · body: `{action:"go_home"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `resetOrigin` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"go_home"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `resetPrinterHeadZ` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"set_z_axis_zero_position"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `resetWaitCount` | POST |  → `/v1/task/resetWaitCount` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `resetWaitCount` | POST |  → `/task/resetWaitCount` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `resetXYLaserHead` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"go_home",waitTime:0}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `resetXYLaserHead` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"laser_head"}` · body: `{action:"go_home",waitTime:0}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `resetZLaserHead` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"go_home_z",waitTime:0}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `resetZLaserHead` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"laser_head"}` · body: `{action:"go_home_z",waitTime:0}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `resumeCamera` | POST |  → `/v1/device/configs/resume_camera` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `resumeCamera` | POST |  → `/config/resume_camera` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `returnMaterial` | POST |  → `/v1/device/returnMaterial` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `rotaryAttachmentModel` | — |  → `n.value?Wr.ra3RollerAutoModeImg:Wr.ra3AutoModeImg` | ? | ·/F1Ultra/·/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `rotaryAttachmentModel` | — |  → `n.value?Wr.ra3RollerManualModeImg:Wr.ra3ManualModeImg` | ? | ·/F1Ultra/·/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `rotaryAttachmentModel` | — |  → `t.value?La.ra3RollerAutoModeImg:La.ra3AutoModeImg` | ? | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `rotaryAttachmentModel` | — |  → `t.value?La.ra3RollerManualModeImg:La.ra3ManualModeImg` | ? | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `rotaryAttachmentModel` | — |  → `t.value?ja.ra3RollerAutoModeImg:ja.ra3AutoModeImg` | ? | ·/·/·/·/·/GS004-CLASS-4/·/·/·/·/·/·/·/·/·/· |
| `rotaryAttachmentModel` | — |  → `t.value?ja.ra3RollerManualModeImg:ja.ra3ManualModeImg` | ? | ·/·/·/·/·/GS004-CLASS-4/·/·/·/·/·/·/·/·/·/· |
| `rotaryAttachmentModel` | — |  → `t.value?Fa.ra3RollerAutoModeImg:Fa.ra3AutoModeImg` | ? | ·/·/·/·/·/·/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `rotaryAttachmentModel` | — |  → `t.value?Fa.ra3RollerManualModeImg:Fa.ra3ManualModeImg` | ? | ·/·/·/·/·/·/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `rotaryAttachmentModel` | — |  → `t.value?Wa.ra3RollerAutoModeImg:Wa.ra3AutoModeImg` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `rotaryAttachmentModel` | — |  → `t.value?Wa.ra3RollerManualModeImg:Wa.ra3ManualModeImg` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `rotaryAttachmentModel` | — |  → `t.value?Dr.ra3RollerAutoModeImg:Dr.ra3AutoModeImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/·/·/·/· |
| `rotaryAttachmentModel` | — |  → `t.value?Dr.ra3RollerManualModeImg:Dr.ra3ManualModeImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/·/·/·/· |
| `rotaryAttachmentModel` | — |  → `t.value?On.ra3RollerAutoModeImg:On.ra3AutoModeImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `rotaryAttachmentModel` | — |  → `t.value?On.ra3RollerManualModeImg:On.ra3ManualModeImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `rotaryAttachmentModel` | — |  → `t.value?ni.ra3RollerAutoModeImg:ni.ra3AutoModeImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `rotaryAttachmentModel` | — |  → `t.value?ni.ra3RollerManualModeImg:ni.ra3ManualModeImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `rotateMakeU` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"go_to",u:26.667,f:2400,waitTime:800}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/P3/·/· |
| `rotateMakeU` | POST |  → `/v1/laser-head/control` | ? · body: `{action:"go_to",u:26.667,f:2400,waitTime:800}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `rotateMakeU` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"laser_head"}` · body: `{action:"go_to",u:21,f:2400,waitTime:800}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `rotateMakeURestore` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"go_to",u:0,f:7200,waitTime:800}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `rotateMakeURestore` | POST |  → `/v1/laser-head/control` | ? · body: `{action:"go_to",u:0,f:7200,waitTime:800}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `runningStatus` | GET |  → `/v1/device/runtime-infos` | ? | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/DT001 |
| `runningStatus` | GET |  → `/device/runningStatus` | ? | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/DT001 |
| `sameDirection` | — |  → `Hje` | ? | ·/F1Ultra/·/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `sameDirection` | — |  → `_5e` | ? | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `sameDirection` | — |  → `LLe` | ? | ·/·/·/·/·/GS004-CLASS-4/·/·/·/·/·/·/·/·/·/· |
| `sameDirection` | — |  → `FLe` | ? | ·/·/·/·/·/·/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `sameDirection` | — |  → `SUe` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `sameDirection` | — |  → `yrt` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `sameDirection` | — |  → `g4t` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `scanAngle` | — |  → `Lbe` | ? | ·/F1Ultra/·/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `scanAngle` | — |  → `Ige` | ? | ·/·/·/GS005/·/·/·/·/·/·/·/·/·/·/·/· |
| `scanAngle` | — |  → `T1e` | ? | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `scanAngle` | — |  → `Bwe` | ? | ·/·/·/·/·/GS004-CLASS-4/·/·/·/·/·/·/·/·/·/· |
| `scanAngle` | — |  → `Xwe` | ? | ·/·/·/·/·/·/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `scanAngle` | — |  → `u3e` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `scanAngle` | — |  → `ebe` | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `securityModule` | POST |  → `/peripheral/securityModule` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setActiveElectric` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"z_tmc_current"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `setActiveElectric` | POST |  → `/peripheral/z_tmc_current` | ? · reply: custom | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `setBatchMode` | PUT |  → `/v1/processing/batch` | ? | ·/F1Ultra/·/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `setBatchMode` | — |  → `/processing/batch` | ? | ·/F1Ultra/·/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `setBeepEnable` | — |  → `/setBeepEnable` | Toggle device beep (`/setBeepEnable`). | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `setBluetoothConnect` | POST | `M9093 A${e} B1` → `/passthrough` (port 8080) | ? | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setBluetoothConnect` | GET | `M9093 A${e (fn)` | ? | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setBluetoothConnect` | POST | `M9093 A${e} B1` → `/v1/parts/control` | ? | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setBluetoothConnect` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Us}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setBluetoothConnect` | POST | `M9093 A${t} B1` → `/passthrough` (port 8080) | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setBluetoothConnect` | GET | `M9093 A${t (fn)` | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setBluetoothConnect` | POST | `M9093 A${t} B1` → `/v1/parts/control` | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setBluetoothConnect` | POST | `M9093 A${n} B1` → `/passthrough` (port 8080) | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setBluetoothConnect` | GET | `M9093 A${n (fn)` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setBluetoothConnect` | POST | `M9093 A${n} B1` → `/v1/parts/control` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setBluetoothConnect` | POST | `M9093 A${r} B1` → `/passthrough` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setBluetoothConnect` | GET | `M9093 A${r (fn)` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setBluetoothConnect` | POST | `M9093 A${r} B1` → `/v1/parts/control` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setBluetoothScanOff` | POST | `M9091 E0` → `/passthrough` (port 8080) | BLE scan off (`M9091 E0`). · body: `{link:"uart485",data_b64:lt({cmd:"M9091 E0",protocol:{type:He.F0F7,prefix:Yn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setBluetoothScanOff` | SET | `M9091 E0` | BLE scan off (`M9091 E0`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setBluetoothScanOff` | POST | `M9091 E0` → `/v1/parts/control` | BLE scan off (`M9091 E0`). · body: `{link:"uart485",data_b64:lt({cmd:"M9091 E0",protocol:{type:He.F0F7,prefix:Yn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setBluetoothScanOff` | POST |  → `/v1/platform/accessories/control` | BLE scan off (`M9091 E0`). · params: `{id:Us}` · body: `{command:"M9091 E0"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setBluetoothScanOn` | POST | `M9091 E1 D180` → `/passthrough` (port 8080) | BLE scan on (`M9091 E1`). · body: `{link:"uart485",data_b64:lt({cmd:"M9091 E1 D180",protocol:{type:He.F0F7,prefix:Yn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setBluetoothScanOn` | SET | `M9091 E1 D180` | BLE scan on (`M9091 E1`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setBluetoothScanOn` | POST | `M9091 E1 D180` → `/v1/parts/control` | BLE scan on (`M9091 E1`). · body: `{link:"uart485",data_b64:lt({cmd:"M9091 E1 D180",protocol:{type:He.F0F7,prefix:Yn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setBluetoothScanOn` | POST |  → `/v1/platform/accessories/control` | BLE scan on (`M9091 E1`). · params: `{id:Us}` · body: `{command:"M9091 E1 D180"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setBluetoothUnbind` | POST | `M9097 A${e}` → `/passthrough` (port 8080) | BLE forget paired device (`M9112`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setBluetoothUnbind` | SET | `M9097 A${e (fn)` | BLE forget paired device (`M9112`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setBluetoothUnbind` | POST | `M9097 A${e}` → `/v1/parts/control` | BLE forget paired device (`M9112`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setBluetoothUnbind` | POST |  → `/v1/platform/accessories/control` | BLE forget paired device (`M9112`). · params: `{id:Us}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setBluetoothUnbind` | POST | `M9097 A${t}` → `/passthrough` (port 8080) | BLE forget paired device (`M9112`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setBluetoothUnbind` | SET | `M9097 A${t (fn)` | BLE forget paired device (`M9112`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setBluetoothUnbind` | POST | `M9097 A${t}` → `/v1/parts/control` | BLE forget paired device (`M9112`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setBluetoothUnbind` | POST | `M9097 A${n}` → `/passthrough` (port 8080) | BLE forget paired device (`M9112`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setBluetoothUnbind` | SET | `M9097 A${n (fn)` | BLE forget paired device (`M9112`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setBluetoothUnbind` | POST | `M9097 A${n}` → `/v1/parts/control` | BLE forget paired device (`M9112`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setBluetoothUnbind` | POST | `M9097 A${r}` → `/passthrough` (port 8080) | BLE forget paired device (`M9112`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setBluetoothUnbind` | SET | `M9097 A${r (fn)` | BLE forget paired device (`M9112`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setBluetoothUnbind` | POST | `M9097 A${r}` → `/v1/parts/control` | BLE forget paired device (`M9112`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setCalibrationData` | POST |  → `/file` | ? · params: `{action:"upload",filename:"points.json"}` · reply: custom | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `setCameraLive` | POST |  → `/v1/platform/camera/live` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setConfig` | PUT |  → `/v1/device/configs` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `setConfig` | POST |  → `/config/set` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `setConfigs` | PUT |  → `/v1/device/configs` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/·/P3/HJ003/· |
| `setConfigs` | POST |  → `/config/set` | ? · reply: {code:0} | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/P2/P2S/P3/HJ003/· |
| `setConfigs` | PUT |  → `/v1/config/set` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `setCutPressBase` | — |  → `cnc/cmd` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `setCuttingPower` | POST |  → `/peripheral/redLaserHead` | ? · body: `{action:"set_cutoff_pwr"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setCuttingPower` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"redLaserHead"}` · body: `{action:"set_cutoff_pwr"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setDebugMode` | PUT |  → `/v1/platform/debug/mode` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `setDeviceName` | PUT |  → `/v1/device/machineInfo` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/P2S/P3/HJ003/DT001 |
| `setDeviceName` | POST |  → `/device/machineInfo` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/P2/P2S/P3/HJ003/DT001 |
| `setDeviceName` | POST |  → `/system` | ? · params: `{action:"set_dev_name"}` | ·/·/·/·/·/·/·/·/M1/·/P1/·/·/·/·/· |
| `setDuctMotorStallDebug` | POST | `M9081 A${e}` → `/passthrough` (port 8080) | Duct-fan stall-detect debug (`M9081`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setDuctMotorStallDebug` | SET | `M9081 A${e (fn)` | Duct-fan stall-detect debug (`M9081`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setDuctMotorStallDebug` | POST | `M9081 A${e}` → `/v1/parts/control` | Duct-fan stall-detect debug (`M9081`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setDuctMotorStallDebug` | POST |  → `/v1/platform/accessories/control` | Duct-fan stall-detect debug (`M9081`). · params: `{id:Za}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setDuctMotorStallDebug` | POST | `M9081 A${t}` → `/passthrough` (port 8080) | Duct-fan stall-detect debug (`M9081`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setDuctMotorStallDebug` | SET | `M9081 A${t (fn)` | Duct-fan stall-detect debug (`M9081`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setDuctMotorStallDebug` | POST | `M9081 A${t}` → `/v1/parts/control` | Duct-fan stall-detect debug (`M9081`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setDuctMotorStallDebug` | POST | `M9081 A${n}` → `/passthrough` (port 8080) | Duct-fan stall-detect debug (`M9081`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setDuctMotorStallDebug` | SET | `M9081 A${n (fn)` | Duct-fan stall-detect debug (`M9081`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setDuctMotorStallDebug` | POST | `M9081 A${n}` → `/v1/parts/control` | Duct-fan stall-detect debug (`M9081`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setDuctMotorStallDebug` | POST | `M9081 A${r}` → `/passthrough` (port 8080) | Duct-fan stall-detect debug (`M9081`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setDuctMotorStallDebug` | SET | `M9081 A${r (fn)` | Duct-fan stall-detect debug (`M9081`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setDuctMotorStallDebug` | POST | `M9081 A${r}` → `/v1/parts/control` | Duct-fan stall-detect debug (`M9081`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setDuctWorkTimeDebug` | POST | `M9085 T${e}` → `/passthrough` (port 8080) | ? | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setDuctWorkTimeDebug` | SET | `M9085 T${e (fn)` | ? | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setDuctWorkTimeDebug` | POST | `M9085 T${e}` → `/v1/parts/control` | ? | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setDuctWorkTimeDebug` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Za}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setDuctWorkTimeDebug` | POST | `M9085 T${t}` → `/passthrough` (port 8080) | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setDuctWorkTimeDebug` | SET | `M9085 T${t (fn)` | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setDuctWorkTimeDebug` | POST | `M9085 T${t}` → `/v1/parts/control` | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setDuctWorkTimeDebug` | POST | `M9085 T${n}` → `/passthrough` (port 8080) | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setDuctWorkTimeDebug` | SET | `M9085 T${n (fn)` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setDuctWorkTimeDebug` | POST | `M9085 T${n}` → `/v1/parts/control` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setDuctWorkTimeDebug` | POST | `M9085 T${r}` → `/passthrough` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setDuctWorkTimeDebug` | SET | `M9085 T${r (fn)` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setDuctWorkTimeDebug` | POST | `M9085 T${r}` → `/v1/parts/control` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setEnvDomain` | PUT |  → `/v1/env/domain` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `setExposure` | POST |  → `/camera/exposure` (port 8329) | ? · params: `{stream:"0"}` · body: `{value:80}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/P3/HJ003/· |
| `setExposure` | PUT |  → `/v1/camera/exposure` | ? · params: `{stream:"0"}` · body: `{stream:"0"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/HJ003/· |
| `setExposureLocal` | POST |  → `/camera/exposure` (port 8329) | ? · params: `{stream:"1"}` · body: `{value:100}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `setExposureLocal` | PUT |  → `/v1/camera/exposure` | ? · body: `{stream:"1",value:100}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `setExposureLocal` | PUT |  → `/v1/peripheral/param` (port 8080) | ? · params: `{type:"fill_light"}` · body: `{name:"nearField",action:"setBri",value:50}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setExposureLocal` | POST |  → `/peripherals/fill_light` (port 8080) | ? · params: `{name:"nearField",action:"setBri",value:50}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setFanBuzzer` | POST | `M9079 S${e}` → `/passthrough` (port 8080) | Duct-fan buzzer (`M9079 S<n>`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setFanBuzzer` | SET | `M9079 S${e (fn)` | Duct-fan buzzer (`M9079 S<n>`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setFanBuzzer` | POST | `M9079 S${e}` → `/v1/parts/control` | Duct-fan buzzer (`M9079 S<n>`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setFanBuzzer` | POST |  → `/v1/platform/accessories/control` | Duct-fan buzzer (`M9079 S<n>`). · params: `{id:su}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setFanBuzzer` | POST | `M9079 S${t}` → `/passthrough` (port 8080) | Duct-fan buzzer (`M9079 S<n>`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setFanBuzzer` | SET | `M9079 S${t (fn)` | Duct-fan buzzer (`M9079 S<n>`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setFanBuzzer` | POST | `M9079 S${t}` → `/v1/parts/control` | Duct-fan buzzer (`M9079 S<n>`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setFanBuzzer` | POST | `M9079 S${n}` → `/passthrough` (port 8080) | Duct-fan buzzer (`M9079 S<n>`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setFanBuzzer` | SET | `M9079 S${n (fn)` | Duct-fan buzzer (`M9079 S<n>`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setFanBuzzer` | POST | `M9079 S${n}` → `/v1/parts/control` | Duct-fan buzzer (`M9079 S<n>`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setFanBuzzer` | POST | `M9079 S${r}` → `/passthrough` (port 8080) | Duct-fan buzzer (`M9079 S<n>`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setFanBuzzer` | SET | `M9079 S${r (fn)` | Duct-fan buzzer (`M9079 S<n>`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setFanBuzzer` | POST | `M9079 S${r}` → `/v1/parts/control` | Duct-fan buzzer (`M9079 S<n>`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setFanBuzzerV3` | POST | `M9079 S${e.value}` → `/passthrough` (port 8080) | ? | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setFanBuzzerV3` | SET | `M9079 S${e (fn)` | ? | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setFanBuzzerV3` | POST | `M9079 S${e.value}` → `/v1/parts/control` | ? | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setFanBuzzerV3` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Za}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setFanBuzzerV3` | POST | `M9079 S${t.value}` → `/passthrough` (port 8080) | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setFanBuzzerV3` | SET | `M9079 S${t (fn)` | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setFanBuzzerV3` | POST | `M9079 S${t.value}` → `/v1/parts/control` | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setFanBuzzerV3` | POST | `M9079 S${n.value}` → `/passthrough` (port 8080) | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setFanBuzzerV3` | SET | `M9079 S${n (fn)` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setFanBuzzerV3` | POST | `M9079 S${n.value}` → `/v1/parts/control` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setFanBuzzerV3` | POST | `M9079 S${r.value}` → `/passthrough` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setFanBuzzerV3` | SET | `M9079 S${r (fn)` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setFanBuzzerV3` | POST | `M9079 S${r.value}` → `/v1/parts/control` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setFanGear` | POST | `M9064 ${e.ctr}${e.gear}` → `/passthrough` (port 8080) | Duct-fan gear (`M9064 A<n>`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setFanGear` | SET | `M9064 ${n (fn)` | Duct-fan gear (`M9064 A<n>`). · body: `n` | F1/·/·/GS005/·/·/·/·/M1/·/·/·/·/·/·/· |
| `setFanGear` | POST | `M9064 ${e.ctr}${e.gear}` → `/v1/parts/control` | Duct-fan gear (`M9064 A<n>`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setFanGear` | POST |  → `/v1/platform/accessories/control` | Duct-fan gear (`M9064 A<n>`). · params: `{id:su}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setFanGear` | POST | `M9064 ${t.ctr}${t.gear}` → `/passthrough` (port 8080) | Duct-fan gear (`M9064 A<n>`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setFanGear` | SET | `M9064 ${e (fn)` | Duct-fan gear (`M9064 A<n>`). · body: `e` | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/P2S/P3/HJ003/· |
| `setFanGear` | POST | `M9064 ${t.ctr}${t.gear}` → `/v1/parts/control` | Duct-fan gear (`M9064 A<n>`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setFanGear` | POST | `M9064 ${n.ctr}${n.gear}` → `/passthrough` (port 8080) | Duct-fan gear (`M9064 A<n>`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setFanGear` | POST | `M9064 ${n.ctr}${n.gear}` → `/v1/parts/control` | Duct-fan gear (`M9064 A<n>`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setFanGear` | SET | `M9064 ${i (fn)` | Duct-fan gear (`M9064 A<n>`). · body: `i` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/DT001 |
| `setFanGear` | SET | `M9064 ${r (fn)` | Duct-fan gear (`M9064 A<n>`). · body: `r` | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `setFanGear` | POST | `M9064 ${r.ctr}${r.gear}` → `/passthrough` (port 8080) | Duct-fan gear (`M9064 A<n>`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setFanGear` | POST | `M9064 ${r.ctr}${r.gear}` → `/v1/parts/control` | Duct-fan gear (`M9064 A<n>`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setFanGearV3` | POST | `M9064 ${e.ctr}${e.gear} ${n}` → `/passthrough` (port 8080) | Duct-fan V3 gear (`M9064 A<n>`). | F1/·/·/GS005/·/·/·/·/M1/·/·/·/·/·/·/· |
| `setFanGearV3` | SET | `M9064 ${n (fn)` | Duct-fan V3 gear (`M9064 A<n>`). · body: `n` | F1/·/·/GS005/·/·/·/·/M1/·/·/·/·/·/·/· |
| `setFanGearV3` | POST | `M9064 ${e.ctr}${e.gear} ${n}` → `/v1/parts/control` | Duct-fan V3 gear (`M9064 A<n>`). | F1/·/·/GS005/·/·/·/·/M1/·/·/·/·/·/·/· |
| `setFanGearV3` | POST |  → `/v1/platform/accessories/control` | Duct-fan V3 gear (`M9064 A<n>`). · params: `{id:Za}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setFanGearV3` | POST | `M9064 ${t.ctr}${t.gear} ${e}` → `/passthrough` (port 8080) | Duct-fan V3 gear (`M9064 A<n>`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setFanGearV3` | SET | `M9064 ${e (fn)` | Duct-fan V3 gear (`M9064 A<n>`). · body: `e` | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/P2S/P3/HJ003/· |
| `setFanGearV3` | POST | `M9064 ${t.ctr}${t.gear} ${e}` → `/v1/parts/control` | Duct-fan V3 gear (`M9064 A<n>`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setFanGearV3` | POST | `M9064 ${n.ctr}${n.gear} ${e}` → `/passthrough` (port 8080) | Duct-fan V3 gear (`M9064 A<n>`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setFanGearV3` | POST | `M9064 ${n.ctr}${n.gear} ${e}` → `/v1/parts/control` | Duct-fan V3 gear (`M9064 A<n>`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setFanGearV3` | POST | `M9064 ${e.ctr}${e.gear} ${i}` → `/passthrough` (port 8080) | Duct-fan V3 gear (`M9064 A<n>`). | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/DT001 |
| `setFanGearV3` | SET | `M9064 ${i (fn)` | Duct-fan V3 gear (`M9064 A<n>`). · body: `i` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/DT001 |
| `setFanGearV3` | POST | `M9064 ${e.ctr}${e.gear} ${i}` → `/v1/parts/control` | Duct-fan V3 gear (`M9064 A<n>`). | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/DT001 |
| `setFanGearV3` | POST | `M9064 ${e.ctr}${e.gear} ${r}` → `/passthrough` (port 8080) | Duct-fan V3 gear (`M9064 A<n>`). | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `setFanGearV3` | SET | `M9064 ${r (fn)` | Duct-fan V3 gear (`M9064 A<n>`). · body: `r` | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `setFanGearV3` | POST | `M9064 ${e.ctr}${e.gear} ${r}` → `/v1/parts/control` | Duct-fan V3 gear (`M9064 A<n>`). | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `setFanGearV3` | POST | `M9064 ${r.ctr}${r.gear} ${e}` → `/passthrough` (port 8080) | Duct-fan V3 gear (`M9064 A<n>`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setFanGearV3` | POST | `M9064 ${r.ctr}${r.gear} ${e}` → `/v1/parts/control` | Duct-fan V3 gear (`M9064 A<n>`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setFanSmokeExhaustTime` | POST |  → `/config/set` (port 8080) | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setFanSmokeExhaustTime` | SET | `M7 D${e (fn)` | ? | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setFanSmokeExhaustTime` | PUT |  → `/v1/device/configs` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setFanSmokeExhaustTime` | PUT |  → `/v1/platform/device/config` | ? · params: `{}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setFanSmokeExhaustTime` | SET | `M7 D${t (fn)` | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setFanSmokeExhaustTime` | SET | `M7 D${n (fn)` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setFanSmokeExhaustTime` | SET | `M7 D${r (fn)` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setFanV3RunDuration` | POST | `M9085 T0` → `/passthrough` (port 8080) | Duct-fan V3 post-run timer (`M9085 T<sec>`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setFanV3RunDuration` | SET | `M9085 T0` | Duct-fan V3 post-run timer (`M9085 T<sec>`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setFanV3RunDuration` | POST | `M9085 T0` → `/v1/parts/control` | Duct-fan V3 post-run timer (`M9085 T<sec>`). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setFanV3RunDuration` | POST |  → `/v1/platform/accessories/control` | Duct-fan V3 post-run timer (`M9085 T<sec>`). · params: `{id:Za}` · body: `{command:"M9085 T0"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setFillLight` | — |  → `/setfilllight` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `setFillLight` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"fill_light"}` · body: `{action:"set_bri"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setFillLight` | POST |  → `/peripheral/fill_light` | ? · body: `{action:"set_bri"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/DT001 |
| `setFillLight` | POST |  → `/v1/peripheral/fill_light` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `setFocusInfo` | PUT |  → `/v1/laser-head/focus/parameter` | ? | F1/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `setFocusInfo` | POST |  → `/focus/control` | ? | F1/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `setGas` | POST |  → `/weld` | ? · body: `{type:"set_weld_gas"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setGas` | PUT |  → `/v1/weld` | ? · body: `{type:"set_weld_gas"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setHandheldPower` | POST |  → `/v1/project/device/accessory/control` | ? · params: `{level:e.level,power:e.power}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setHeaterTemp` | POST |  → `/v1/peripheral/heater_temp` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `setHeaterTemp` | POST |  → `/peripheral/heater_temp` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `setLaserHead` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"go_to",waitTime:3e4,f:S0}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/P3/HJ003/· |
| `setLaserHead` | POST |  → `/v1/laser-head/control` | ? · body: `{action:"go_to",waitTime:3e4,f:uf}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `setLaserHead` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"laser_head"}` · body: `{action:"go_to",waitTime:3e4,f:La}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/HJ003/· |
| `setLaserParameters` | — |  → `/mode` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `setLaserPower` | — |  → `/setlaserpower` | ? | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `setMachineInfo` | POST |  → `/device/machineInfo` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/P3/HJ003/DT001 |
| `setMachineInfo` | PUT |  → `/v1/device/machineInfo` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/HJ003/DT001 |
| `setMachiningPower` | POST |  → `/v1/project/device/accessory/control` | ? · params: `{power:e.power}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setMultiFunctionalBaseGear` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Vv}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setOffset` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"crossred_Offset"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `setOffset` | POST |  → `/peripheral/crossred_Offset` | ? · reply: custom | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `setPeripheralParameter` | PUT |  → `/v1/peripheral/param` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `setPowerResume` | PUT |  → `/v1/processing/powerResume` | ? | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `setPowerResume` | — |  → `/processing/powerResume` | ? | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `setPrinterCalibrated` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"set_calibrated",calibrated:!0}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setPrinterRedCrossOffset` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"set_crossred_shift",xend_shift:0,yend_shift:0,xtop_shift:0,ytop_shift:0,z_shift:0}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setPrinterTouchShift` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"set_touch_shift"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setPurifierCheck` | — |  → `/setpurifiercheck` | ? | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `setPurifierContinue` | — |  → `/setpurifiercontinue` | ? | ·/·/·/·/·/·/·/·/M1/·/P1/·/·/·/·/· |
| `setPurifierFilterLifeDebug` | SET | `M9034 A${e (fn)` | ? | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setPurifierFilterLifeDebug` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:rg}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setPurifierFilterLifeDebug` | SET | `M9034 A${t (fn)` | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setPurifierFilterLifeDebug` | SET | `M9034 A${n (fn)` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setPurifierFilterLifeDebug` | SET | `M9034 A${r (fn)` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setPurifierGear` | POST | `M9039 ${e}` → `/passthrough` (port 8080) | AP2 V2 purifier speed (`M9039 <gear>`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setPurifierGear` | SET | `M9039 ${e (fn)` | AP2 V2 purifier speed (`M9039 <gear>`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setPurifierGear` | POST | `M9039 ${e}` → `/v1/parts/control` | AP2 V2 purifier speed (`M9039 <gear>`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setPurifierGear` | POST |  → `/v1/platform/accessories/control` | AP2 V2 purifier speed (`M9039 <gear>`). · params: `{id:rg}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setPurifierGear` | POST | `M9039 ${t}` → `/passthrough` (port 8080) | AP2 V2 purifier speed (`M9039 <gear>`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setPurifierGear` | SET | `M9039 ${t (fn)` | AP2 V2 purifier speed (`M9039 <gear>`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setPurifierGear` | POST | `M9039 ${t}` → `/v1/parts/control` | AP2 V2 purifier speed (`M9039 <gear>`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setPurifierGear` | POST | `M9039 ${n}` → `/passthrough` (port 8080) | AP2 V2 purifier speed (`M9039 <gear>`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setPurifierGear` | SET | `M9039 ${n (fn)` | AP2 V2 purifier speed (`M9039 <gear>`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setPurifierGear` | POST | `M9039 ${n}` → `/v1/parts/control` | AP2 V2 purifier speed (`M9039 <gear>`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setPurifierGear` | POST | `M9039 ${r}` → `/passthrough` (port 8080) | AP2 V2 purifier speed (`M9039 <gear>`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setPurifierGear` | SET | `M9039 ${r (fn)` | AP2 V2 purifier speed (`M9039 <gear>`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setPurifierGear` | POST | `M9039 ${r}` → `/v1/parts/control` | AP2 V2 purifier speed (`M9039 <gear>`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setPurifierV3Buzzer` | POST | `M9046 F${e}` → `/passthrough` (port 8080) | Purifier V3 buzzer toggle (`M9046`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setPurifierV3Buzzer` | SET | `M9046 F${e (fn)` | Purifier V3 buzzer toggle (`M9046`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setPurifierV3Buzzer` | POST | `M9046 F${e}` → `/v1/parts/control` | Purifier V3 buzzer toggle (`M9046`). | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setPurifierV3Buzzer` | POST |  → `/v1/platform/accessories/control` | Purifier V3 buzzer toggle (`M9046`). · params: `{id:nl}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setPurifierV3Buzzer` | POST | `M9046 F${t}` → `/passthrough` (port 8080) | Purifier V3 buzzer toggle (`M9046`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setPurifierV3Buzzer` | SET | `M9046 F${t (fn)` | Purifier V3 buzzer toggle (`M9046`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setPurifierV3Buzzer` | POST | `M9046 F${t}` → `/v1/parts/control` | Purifier V3 buzzer toggle (`M9046`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setPurifierV3Buzzer` | POST | `M9046 F${n}` → `/passthrough` (port 8080) | Purifier V3 buzzer toggle (`M9046`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setPurifierV3Buzzer` | SET | `M9046 F${n (fn)` | Purifier V3 buzzer toggle (`M9046`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setPurifierV3Buzzer` | POST | `M9046 F${n}` → `/v1/parts/control` | Purifier V3 buzzer toggle (`M9046`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setPurifierV3Buzzer` | POST | `M9046 F${r}` → `/passthrough` (port 8080) | Purifier V3 buzzer toggle (`M9046`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setPurifierV3Buzzer` | SET | `M9046 F${r (fn)` | Purifier V3 buzzer toggle (`M9046`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setPurifierV3Buzzer` | POST | `M9046 F${r}` → `/v1/parts/control` | Purifier V3 buzzer toggle (`M9046`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${n} A${n} B${r} C${t}` → `/passthrough` (port 8080) | ? | F1/·/·/GS005/·/·/·/·/M1/·/·/·/·/·/·/· |
| `setPurifierV3FilterLifeDebug` | SET | `M9055 W${n} (fn)` | ? | F1/·/·/GS005/·/·/·/·/M1/·/·/·/·/·/·/· |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${n} A${n} B${r} C${t}` → `/v1/parts/control` | ? | F1/·/·/GS005/·/·/·/·/M1/·/·/·/·/·/·/· |
| `setPurifierV3FilterLifeDebug` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:nl}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${e} A${e} B${n} C${i}` → `/passthrough` (port 8080) | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `setPurifierV3FilterLifeDebug` | SET | `M9055 W${e} (fn)` | ? | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/P2S/P3/HJ003/· |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${e} A${e} B${n} C${i}` → `/v1/parts/control` | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${e} A${e} B${t} C${i}` → `/passthrough` (port 8080) | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${e} A${e} B${t} C${i}` → `/v1/parts/control` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${i} A${i} B${r} C${t}` → `/passthrough` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `setPurifierV3FilterLifeDebug` | SET | `M9055 W${i} (fn)` | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/DT001 |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${i} A${i} B${r} C${t}` → `/v1/parts/control` | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${r} A${r} B${n} C${t}` → `/passthrough` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `setPurifierV3FilterLifeDebug` | SET | `M9055 W${r} (fn)` | ? | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${r} A${r} B${n} C${t}` → `/v1/parts/control` | ? | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${e} A${e} B${t} C${r}` → `/passthrough` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/·/·/·/· |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${e} A${e} B${t} C${r}` → `/v1/parts/control` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/·/·/·/· |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${e} A${e} B${t} C${n}` → `/passthrough` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${e} A${e} B${t} C${n}` → `/v1/parts/control` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${e} A${e} B${n} C${r}` → `/passthrough` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${e} A${e} B${n} C${r}` → `/v1/parts/control` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${i} A${i} B${n} C${t}` → `/passthrough` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `setPurifierV3FilterLifeDebug` | POST | `M9055 W${i} A${i} B${n} C${t}` → `/v1/parts/control` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `setPurifierV3Gear` | POST | `M9039 ${e}` → `/passthrough` (port 8080) | ? | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setPurifierV3Gear` | SET | `M9039 ${e (fn)` | ? | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setPurifierV3Gear` | POST | `M9039 ${e}` → `/v1/parts/control` | ? | F1/·/·/GS005/·/·/·/·/M1/M1Ultra/P1/·/·/·/·/DT001 |
| `setPurifierV3Gear` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:nl}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `setPurifierV3Gear` | POST | `M9039 ${t}` → `/passthrough` (port 8080) | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setPurifierV3Gear` | SET | `M9039 ${t (fn)` | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setPurifierV3Gear` | POST | `M9039 ${t}` → `/v1/parts/control` | ? | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setPurifierV3Gear` | POST | `M9039 ${n}` → `/passthrough` (port 8080) | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setPurifierV3Gear` | SET | `M9039 ${n (fn)` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setPurifierV3Gear` | POST | `M9039 ${n}` → `/v1/parts/control` | ? | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `setPurifierV3Gear` | POST | `M9039 ${r}` → `/passthrough` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setPurifierV3Gear` | SET | `M9039 ${r (fn)` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setPurifierV3Gear` | POST | `M9039 ${r}` → `/v1/parts/control` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `setRedDotPower` | POST |  → `/weld` | ? · body: `{type:"set_ir_power"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setRedDotPower` | PUT |  → `/v1/weld` | ? · body: `{type:"set_ir_power"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setRedDotStatus` | POST |  → `/weld` | ? · body: `{type:"set_ir_power_flag"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setRedDotStatus` | PUT |  → `/v1/weld` | ? · body: `{type:"set_ir_power_flag"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setRedLaserFocalOffset` | POST |  → `/peripheral/redLaserHead` | ? · body: `{action:"set_focus_z_offset"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setRedLaserFocalOffset` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"redLaserHead"}` · body: `{action:"set_focus_z_offset"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setRedLaserXYOffset` | POST |  → `/peripheral/redLaserHead` | ? · body: `{action:"set_location_offset"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setRedLaserXYOffset` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"redLaserHead"}` · body: `{action:"set_location_offset"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setRoaster` | PUT |  → `/v1/device/roaster` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `setRoaster` | POST |  → `/device/roaster` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `setSpeaker` | POST |  → `/v1/peripheral/speaker` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `setSpeaker` | POST |  → `/peripheral/speaker` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `setToolType` | PUT |  → `/v1/processing/printToolType` | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `setToolType` | — |  → `/setprintToolType` | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `setUGearRatio` | PUT |  → `/v1/peripheral/param` (port 8080) | ? · params: `{type:"laser_head"}` · body: `{action:"setUAxisPosRatio"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setUGearRatio` | POST |  → `/peripheral/laser_head` (port 8080) | ? · body: `{action:"setUAxisPosRatio"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setUploadFileParams` | PUT |  → `/v1/processing/upload/config` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `setUserConfigs` | PUT |  → `/v1/device/configs` | ? | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `setUserConfigs` | POST |  → `/config/set` | ? · body: `{alias:"config",type:"user",kv:{}}` | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `setUZero` | PUT |  → `/v1/peripheral/param` (port 8080) | ? · params: `{type:"laser_head"}` · body: `{action:"setAxisPos",u:0}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setUZero` | POST |  → `/peripheral/laser_head` (port 8080) | ? · body: `{action:"setAxisPos",u:0}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setVideoRate` | POST |  → `/rate` (port 8329) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setVideoResolution` | POST |  → `/v1/res` (port 8329) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setVideoResolution` | POST |  → `/v1/camera/params/res` (port 8329) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setVideoResolution` | POST |  → `/res` (port 8329) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `setWeldFocus` | POST |  → `/weld` | ? · body: `{type:"set_weld_focus_flag"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setWeldFocus` | PUT |  → `/v1/weld` | ? · body: `{type:"set_weld_focus_flag"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setWideCleaning` | POST |  → `/weld` | ? · body: `{type:"set_wide_cleaning_flag"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setWideCleaning` | PUT |  → `/v1/weld` | ? · body: `{type:"set_wide_cleaning_flag"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `setWorkMode` | — |  → `/setprintToolType` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `shakeCompensation` | POST |  → `/vibration` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `shakeCompensation` | POST |  → `/v1/vibration` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `shakeHands` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"shake_hands"}` · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `sleepWakeUp` | — |  → `/sleepwakeup` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `smallLensesProcessSetting` | PUT |  → `/v1/processing/inner-engrave` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `smallLensesProcessSetting` | POST |  → `/calibration_setting` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `smokeFan` | POST |  → `/v1/peripheral/smoke_fan` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `smokeFan` | POST |  → `/peripheral/smoke_fan` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `speed` | — |  → `NNe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `startAccessoryFirmwareUpgrade` | POST |  → `/v1/platform/accessories/upgrade` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `startAutoFocus` | POST |  → `/v1/laser-head/focus/control` | ? · body: `{action:"auto_start"}` | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `startCalibration` | POST |  → `/v1/calibration/start` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `startCalibration` | POST |  → `/calibration/start` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `startPrint` | — |  → `/cnc/data?action=start` | Start processing (`/cnc/data?action=start`). | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `startPrint` | POST |  → `/v1/processing/start` | Start processing (`/cnc/data?action=start`). | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `startPrint` | POST |  → `/processing/start` | Start processing (`/cnc/data?action=start`). · reply: custom | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `startProcess` | PUT |  → `/v1/processing/state` | ? · params: `{action:"start"}` | F1/F1Ultra/GS003/GS005/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/P3/·/· |
| `startRecord` | — |  → `v1/recordctrl` (port 8089) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `startRedLaserUpgrade` | GET |  → `parts?machine=23&type=732E&blockID=2E35&blockLen=128&filename=laser.bin` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `startRedLaserUpgrade` | POST |  → `/v1/parts/firmware/upgrade` | ? · body: `{machine:23,type:"732E",blockID:"2E35",blockLen:128,filename:"laser.bin"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `startRIP` | GET |  → `/v1/task/rip` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `startRIP` | GET |  → `/task/rip` | ? · reply: custom | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `startTask` | GET |  → `/v1/task/start` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `startTask` | GET |  → `/task/start` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `status` | — |  → `/cnc/status` | ? · reply: JSON | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `stopAutoFocus` | POST |  → `/v1/laser-head/focus/control` | ? · body: `{action:"auto_stop"}` | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `stopBedLeveling` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"bottomBed"}` · body: `{action:"stop"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `stopBedLeveling` | POST |  → `/peripheral/bottomBed` | ? · body: `{action:"stop"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `stopRecord` | — |  → `v1/recordctrl` (port 8089) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `stopWalkBorder` | PUT |  → `/v1/processing/state` | Stop framing. · params: `{action:"stop"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `stopWalkBorder` | POST |  → `/processing/stop` | Stop framing. | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/P3/·/· |
| `stopWalkBorder` | POST |  → `/v1/processing/stop` | Stop framing. | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `strengthenEnable` | — |  → `RUe` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `subdivide` | — |  → `rUe` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `switchHead` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"workhead_Zchange"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `switchHead` | POST |  → `/peripheral/workhead_Zchange` | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `syncTime` | POST |  → `/time/sync` | ? | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `taperSettings` | — |  → `Wr.taperBuilderTipsImg` | ? | ·/F1Ultra/·/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `taperSettings` | — |  → `Wr.cylinderBuilderTipsMp4` | ? | ·/F1Ultra/·/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `taperSettings` | — |  → `La.taperBuilderTipsImg` | ? | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `taperSettings` | — |  → `La.cylinderBuilderTipsMp4` | ? | ·/·/·/·/GS006/·/·/·/·/·/·/·/·/·/·/· |
| `taperSettings` | — |  → `ja.taperBuilderTipsImg` | ? | ·/·/·/·/·/GS004-CLASS-4/·/·/·/·/·/·/·/·/·/· |
| `taperSettings` | — |  → `ja.cylinderBuilderTipsMp4` | ? | ·/·/·/·/·/GS004-CLASS-4/·/·/·/·/·/·/·/·/·/· |
| `taperSettings` | — |  → `Fa.taperBuilderTipsImg` | ? | ·/·/·/·/·/·/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `taperSettings` | — |  → `Fa.cylinderBuilderTipsMp4` | ? | ·/·/·/·/·/·/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `taperSettings` | — |  → `Wa.taperBuilderTipsImg` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `taperSettings` | — |  → `Wa.cylinderBuilderTipsMp4` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `taperSettings` | — |  → `Dr.taperBuilderTipsImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/·/·/·/· |
| `taperSettings` | — |  → `Dr.cylinderBuilderTipsMp4` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/·/·/·/· |
| `taperSettings` | — |  → `On.taperBuilderTipsImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `taperSettings` | — |  → `On.cylinderBuilderTipsMp4` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `taperSettings` | — |  → `ni.taperBuilderTipsImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `taperSettings` | — |  → `ni.cylinderBuilderTipsMp4` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `taskCacheInfo` | POST |  → `/v1/task/cacheInfo` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `taskCacheInfo` | GET |  → `/task/cacheInfo` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `testSingleLaser` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"laser_ctrl"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `testSingleLaser` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"laser_head"}` · body: `{action:"laser_ctrl"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `thickness` | — |  → `s.ext.commonResource.thicknessImg` | ? | F1/·/·/·/·/·/·/·/·/·/·/·/·/·/·/· |
| `thickness` | — |  → `e.ext.commonResource.thicknessImg` | ? | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/·/·/·/·/P2/P2S/·/·/· |
| `thickness` | — |  → `t.ext.commonResource.thicknessImg` | ? | ·/·/·/GS005/GS006/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `thickness` | — |  → `n.ext.commonResource.autoMeasureImg` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `thickness` | — |  → `r.ext.commonResource.thicknessImg` | ? | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `thickness` | — |  → `e.value.commonResource.thicknessImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `thickness` | — |  → `u.commonResource.thicknessImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `thicknessGroupNew` | — |  → `e.ext.commonResource.thicknessImg` | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `thicknessMeasure` | — |  → `n.ext.commonResource.autoMeasureImg` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `toOriginPoint` | — | `(fn)` → `/cnc/cmd?cmd=G0 X0 Y0` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `transferFinished` | GET |  → `/v1/task/transfered` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `transferFinished` | GET |  → `/task/transfered` | ? · reply: custom | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `triggerProcessing` | POST |  → `/peripheral/button` (port 8080) | ? · body: `{action:"short"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `triggerProcessing` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"button"}` · body: `{action:"short"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `triggerReport` | POST | `M9064` → `/passthrough` (port 8080) | ? · body: `{link:"uart485",data_b64:lt({cmd:"M9064",protocol:{type:He.F0F7,prefix:yn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `triggerReport` | SET | `M9064` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `triggerReport` | POST | `M9064` → `/v1/parts/control` | ? · body: `{link:"uart485",data_b64:lt({cmd:"M9064",protocol:{type:He.F0F7,prefix:yn}})}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `triggerReport` | POST |  → `/v1/platform/accessories/control` | ? · params: `{id:Za}` · body: `{command:"M9064"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `turnLight` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"fill_light"}` · body: `{action:"set_bri",idx:0,value:255}` | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/P2S/P3/HJ003/· |
| `turnLight` | POST |  → `/peripheral/fill_light` | ? · body: `{action:"set_bri",idx:0,value:255}` | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/P2S/P3/HJ003/· |
| `turnLight` | — | `(fn)` → `/cnc/cmd?cmd=M13 S255 S255` | ? · params: `{t:+new Date}` | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `turnOffCoaxialIr` | POST |  → `peripheral/coaxial_Ir` (port 8080) | ? · body: `{action:"off"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `turnOffCoaxialIr` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"coaxial_Ir"}` · body: `{action:"off"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `turnOffCoaxialIr` | POST |  → `/peripheral/coaxialRedLight` (port 8080) | ? · body: `{action:"off"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `turnOffGlobalIrLed` | POST |  → `/peripheral/ir_led` | ? · body: `{action:"off",index:2}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `turnOffGlobalIrLed` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"ir_led"}` · body: `{action:"off",index:2}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `turnOffLocalIrLed` | POST |  → `/peripheral/ir_led` | ? · body: `{action:"off",index:1}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `turnOffLocalIrLed` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"ir_led"}` · body: `{action:"off",index:1}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `turnOffLocalIrLed` | POST |  → `/peripheral/ir_measure_distance` | ? · body: `{action:"ir_light_control",type:"off"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `turnOnCoaxialIr` | POST |  → `peripheral/coaxial_Ir` (port 8080) | ? · body: `{action:"on"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `turnOnCoaxialIr` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"coaxial_Ir"}` · body: `{action:"on"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `turnOnCoaxialIr` | POST |  → `/peripheral/coaxialRedLight` (port 8080) | ? · body: `{action:"on"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `turnOnGlobalIrLed` | POST |  → `/peripheral/ir_led` | ? · body: `{action:"on",index:2}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `turnOnGlobalIrLed` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"ir_led"}` · body: `{action:"on",index:2}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `turnOnLocalIrLed` | POST |  → `/peripheral/ir_led` | ? · body: `{action:"on",index:1}` | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `turnOnLocalIrLed` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"ir_led"}` · body: `{action:"on",index:1}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `turnOnLocalIrLed` | POST |  → `/peripheral/ir_measure_distance` | ? · body: `{action:"ir_light_control",type:"on"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `uAxisSwitch` | POST |  → `/peripheral/uAxisSwitch` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `unLockedLeaserHead` | — | `(fn)` → `/cnc/cmd?cmd=M110 S12` | ? | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `unlockShaft` | POST |  → `/peripheral/laser_head` | ? · body: `{action:"unlock_motor"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `unlockShaft` | PUT |  → `/v1/peripheral/param` | ? · params: `{type:"laser_head"}` · body: `{action:"unlock_motor"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `updateAccessoryFirmware` | POST |  → `/v1/parts/firmware/upgrade` | ? | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `updateAccessoryFirmware` | — |  → `/parts` (port 8080) | ? | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/P2/P2S/P3/·/DT001 |
| `updateAccessoryFirmware` | GET |  → `/v1/parts` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `updateCalibData` | POST |  → `/v1/calibration/update` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `updateCalibData` | POST |  → `/calibration/update` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `updateFirmware` | POST |  → `/package` (port 8087) | REST family /package?action=burn (raw blob body). · params: `{action:"burn"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/P2/P2S/P3/HJ003/DT001 |
| `updateFirmwareHandshake` | PUT |  → `/v1/device/upgrade-mode` | REST handshake (`/upgrade_version?force_upgrade=1[&machine_type=...]`). · params: `{mode:"ready"}` · body: `{machine_type:"MXF"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/P2S/P3/HJ003/DT001 |
| `updateFirmwareHandshake` | — |  → `/upgrade_version` (port 8087) | REST handshake (`/upgrade_version?force_upgrade=1[&machine_type=...]`). · params: `{force_upgrade:"1"}` · reply: custom | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/·/P2/P2S/P3/HJ003/DT001 |
| `updateFirmWareProgress` | GET |  → `/v1/parts/firmware/upgrade-progress` | Flash progress query (`/system?action=get_upgrade_progress`). · body: `o` · reply: JSON | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `updateFirmWareProgress` | — |  → `/partsProgress` (port 8080) | Flash progress query (`/system?action=get_upgrade_progress`). · body: `s` · reply: JSON | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/P2/P2S/P3/·/DT001 |
| `updateFirmWareProgress` | GET |  → `/v1/partsProgress` (port 8080) | Flash progress query (`/system?action=get_upgrade_progress`). · body: `l` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `updateOptimizeFan` | POST | `M9066 A${e.gear} ${n} T${e.time||0}` → `/passthrough` (port 8080) | Trigger fan optimisation routine (`M9066`). | F1/·/·/GS005/·/·/·/·/M1/·/·/·/·/·/·/· |
| `updateOptimizeFan` | SET | `M9066 A${n (fn)` | Trigger fan optimisation routine (`M9066`). · body: `n` | F1/·/·/GS005/·/·/·/·/M1/·/·/·/·/·/·/· |
| `updateOptimizeFan` | POST | `M9066 A${e.gear} ${n} T${e.time||0}` → `/v1/parts/control` | Trigger fan optimisation routine (`M9066`). | F1/·/·/GS005/·/·/·/·/M1/·/·/·/·/·/·/· |
| `updateOptimizeFan` | POST |  → `/v1/platform/accessories/control` | Trigger fan optimisation routine (`M9066`). · params: `{id:Za}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/P1/P2/P2S/P3/HJ003/DT001 |
| `updateOptimizeFan` | POST | `M9066 A${t.gear} ${e} T${t.time||0}` → `/passthrough` (port 8080) | Trigger fan optimisation routine (`M9066`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `updateOptimizeFan` | SET | `M9066 A${e (fn)` | Trigger fan optimisation routine (`M9066`). · body: `e` | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/P2S/P3/HJ003/· |
| `updateOptimizeFan` | POST | `M9066 A${t.gear} ${e} T${t.time||0}` → `/v1/parts/control` | Trigger fan optimisation routine (`M9066`). | ·/F1Ultra/GS003/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `updateOptimizeFan` | POST | `M9066 A${n.gear} ${e} T${n.time||0}` → `/passthrough` (port 8080) | Trigger fan optimisation routine (`M9066`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `updateOptimizeFan` | POST | `M9066 A${n.gear} ${e} T${n.time||0}` → `/v1/parts/control` | Trigger fan optimisation routine (`M9066`). | ·/·/·/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/P2/·/·/·/· |
| `updateOptimizeFan` | POST | `M9066 A${e.gear} ${i} T${e.time||0}` → `/passthrough` (port 8080) | Trigger fan optimisation routine (`M9066`). | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/DT001 |
| `updateOptimizeFan` | SET | `M9066 A${i (fn)` | Trigger fan optimisation routine (`M9066`). · body: `i` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/DT001 |
| `updateOptimizeFan` | POST | `M9066 A${e.gear} ${i} T${e.time||0}` → `/v1/parts/control` | Trigger fan optimisation routine (`M9066`). | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/DT001 |
| `updateOptimizeFan` | POST | `M9066 A${e.gear} ${r} T${e.time||0}` → `/passthrough` (port 8080) | Trigger fan optimisation routine (`M9066`). | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `updateOptimizeFan` | SET | `M9066 A${r (fn)` | Trigger fan optimisation routine (`M9066`). · body: `r` | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `updateOptimizeFan` | POST | `M9066 A${e.gear} ${r} T${e.time||0}` → `/v1/parts/control` | Trigger fan optimisation routine (`M9066`). | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `updateOptimizeFan` | POST | `M9066 A${r.gear} ${e} T${r.time||0}` → `/passthrough` (port 8080) | Trigger fan optimisation routine (`M9066`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `updateOptimizeFan` | POST | `M9066 A${r.gear} ${e} T${r.time||0}` → `/v1/parts/control` | Trigger fan optimisation routine (`M9066`). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/P3/·/· |
| `upgradePrinterFirmware` | POST |  → `/peripheral/four_colour_printer` | ? · body: `{action:"upgrade_printer",file_path:"/tmp/test.bin"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `uploadAccessoryFirmware` | POST |  → `/upload` (port 8080) | ? · reply: {code:0} | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/P2/P2S/P3/·/DT001 |
| `uploadAccessoryFirmware` | POST |  → `/v1/upload` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `uploadCalibrationFile` | POST |  → `/file` | ? · params: `{action:"upload"}` · reply: custom | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `uploadFile` | POST |  → `/file` (port 8080) | ? · params: `{action:"upload"}` | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `uploadFirmwareBurn` | — |  → `/burn?reboot=true` (port 8087) | M1 4-step: POST /burn?reboot=true. · reply: custom | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `uploadFirmwarePackage` | POST |  → `/package` (port 8087) | M1 4-step: POST /package (raw body). · reply: custom | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `uploadFirmwareScript` | POST |  → `/script` (port 8087) | M1 4-step: POST /script (raw body). · reply: custom | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `uploadFrameGcode` | PUT |  → `/v1/processing/upload/config` | ? · params: `{fileType:1,fileName:"tmpFrame.gcode"}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `uploadFrameGcode` | POST |  → `/processing/upload` (port 8080) | ? · params: `{gcodeType:"frame",fileType:"txt",zip:!1}` | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `uploadGcode` | PUT |  → `/v1/processing/upload/config` | ? · params: `{fileType:1,fileName:"tmp.gcode"}` | F1/·/·/GS005/·/·/·/GS009-CLASS-4/·/M1Ultra/·/·/P2S/P3/HJ003/· |
| `uploadGcode` | POST |  → `/processing/upload` (port 8080) | ? · params: `{gcodeType:"processing",fileType:"txt"}` | F1/·/·/GS005/·/·/·/GS009-CLASS-4/·/M1Ultra/·/P2/P2S/P3/HJ003/· |
| `uploadGCode` | PUT |  → `/v1/processing/upload/config` | ? · params: `{fileType:1,fileName:"tmp.xf"}` | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `uploadGCode` | POST |  → `/processing/upload` (port 8080) | ? · params: `{gcodeType:"processing",fileType:"xf"}` | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `uploadGcode` | POST |  → `/cnc/data?action=upload&id=-1` | ? · reply: custom | ·/·/·/·/·/·/·/·/M1/·/·/·/·/·/·/· |
| `uploadGcode` | POST |  → `/cnc/data?action=upload&zip=false&id=-1` (port 8080) | ? · reply: custom | ·/·/·/·/·/·/·/·/·/·/P1/·/·/·/·/· |
| `uploadGCodeByText` | PUT |  → `/v1/processing/upload/config` | ? · params: `{fileType:1,fileName:"tmp.gcode"}` | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/P3/·/· |
| `uploadGCodeByText` | POST |  → `/processing/upload` (port 8080) | ? · params: `{gcodeType:"processing",fileType:"txt"}` | ·/F1Ultra/GS003/·/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/P3/·/· |
| `uploadPdFile` | POST |  → `/task/upload` | ? · reply: custom | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `uploadPrinterFirmware` | POST |  → `/peripheral/four_colour_printer?action=upload&file_path=/tmp/test.bin` | ? · reply: JSON | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `uploadPrx` | POST |  → `/processing/upload` | ? · reply: custom | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `uploadRedLaserFirmware` | POST |  → `/upload` (port 8080) | ? · reply: {code:0} | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `uploadWalkBorder` | PUT |  → `/v1/processing/upload/config` | ? · params: `{fileType:1,fileName:"tmpFrame.gcode"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/P3/HJ003/· |
| `uploadWalkBorder` | POST |  → `/processing/upload` | ? · params: `{gcodeType:"frame",fileType:"txt",autoStart:"1",loopPrint:"1"}` | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/P3/HJ003/· |
| `uvReady` | GET |  → `/v1/device/uv-laser` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `uvReady` | — |  → `/uv_ready` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `version` | GET |  → `/v1/device/machineInfo` | Firmware version (`/system?action=version_v2` or M99). | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/M1Ultra/·/·/·/·/·/· |
| `version` | — |  → `/system` | Firmware version (`/system?action=version_v2` or M99). · params: `{action:"version_v2"}` · reply: custom | F1/F1Ultra/GS003/GS005/GS006/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/M1/M1Ultra/·/P2/P2S/P3/HJ003/DT001 |
| `version` | GET |  → `/v1/system/version_v2` | Firmware version (`/system?action=version_v2` or M99). | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `version` | GET |  → `/v1/system` | Firmware version (`/system?action=version_v2` or M99). · params: `{action:"version_v2"}` | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/HJ003/· |
| `videoProcessStatus` | GET |  → `/debug` (port 8329) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `videoStream` | GET |  → `/v1/video` (port 8329) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `videoStream` | GET |  → `/video` (port 8329) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |
| `waterInInkStack` | POST |  → `/v1/peripheral/water_in_pump` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `waterInInkStack` | POST |  → `/peripheral/water_in_pump` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `whiteInkLoop` | POST |  → `/v1/peripheral/ink_loop_pump` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `whiteInkLoop` | POST |  → `/peripheral/ink_loop_pump` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `whiteInkShrink` | — |  → `nCe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `whiteInkShrinkReverse` | — |  → `aCe` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `whiteStirring` | POST |  → `/v1/peripheral/ink_stir_pump` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `whiteStirring` | POST |  → `/peripheral/ink_stir_pump` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `wobbleEnable` | — |  → `b2e` | ? | ·/·/·/·/·/GS004-CLASS-4/·/·/·/·/·/·/·/·/·/· |
| `wobbleEnable` | — |  → `x2e` | ? | ·/·/·/·/·/·/GS007-CLASS-4/·/·/·/·/·/·/·/·/· |
| `wobbleEnable` | — |  → `O3e` | ? | ·/·/·/·/·/·/·/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `workHead` | POST |  → `/v1/peripheral/work_head` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `workHead` | POST |  → `/peripheral/work_head` | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/·/·/DT001 |
| `workingInfo` | GET |  → `/v1/device/statistics` | ? | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `workingInfo` | — |  → `/device/workingInfo` | ? | ·/F1Ultra/GS003/·/·/GS004-CLASS-4/GS007-CLASS-4/GS009-CLASS-4/·/·/·/·/·/·/·/· |
| `writeSnCode` | PUT |  → `/v1/device/configs` | ? | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `writeSnCode` | POST |  → `/config/set` | ? · reply: custom | ·/·/·/·/·/·/·/·/·/M1Ultra/·/·/·/·/·/· |
| `zipCameraData` | GET | `(fn)` → `/debug?action=systemHelper&cmd=tar zcvf /tmp/config.gz /config/data/ machinetype.txt&type=2&password=hulurobot` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/P2/P2S/·/·/· |
| `zipCameraData` | POST |  → `/v1/device/operate-log` | ? · body: `{action:"zipCameraData"}` | ·/·/·/·/·/·/·/·/·/·/·/·/P2S/·/·/· |
| `zipCameraData` | GET | `(fn)` → `/debug?action=systemHelper&cmd=tar zcvf /tmp/config.gz /config/data /config/machinetype.txt&type=2&password=hulurobot` (port 8080) | ? | ·/·/·/·/·/·/·/·/·/·/·/·/·/P3/·/· |


---

## Bluetooth dongle (peripheral M-code reference)

The optional xTool Bluetooth dongle exposes its own M-code family. Used by the S1 (and probably future models) to scan + pair external accessories like xTouch, foot pedal, remote scanner.

| Code | Effect |
|---|---|
| `M9091 E1 D180` | Start BLE scan (E=enable, D=duration s) |
| `M9091 E0` | Stop scan |
| `M9092 T<ms>` | List nearby parts (T = scan-window ms) |
| `M9093 A<MAC> B<1>` | Connect to MAC |
| `M9094 A<MAC>` | Saved devices list |
| `M9095` | Get currently connected list |
| `M9096 A<MAC>` | Pair / forget specific MAC |
| `M9097 A<MAC>` | Probe specific MAC |
| `M9098` | Connected devices snapshot |

These codes are sent via the dongle's own `startCmd` prefix; XCS treats
the dongle as a sub-protocol behind the host MCU.

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
  "domain": "atomm",
  "region": "en",
  "contentId": "xTool-d2-firmware",
  "deviceId": "<serial>",
  "packages": [
    {"contentId": "xTool-d2-0x20", "contentVersion": "<dot-version>"},
    {"contentId": "xTool-d2-0x21", "contentVersion": "<dot-version>"},
    {"contentId": "xTool-d2-0x22", "contentVersion": "<dot-version>"}
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
    "id": "xTool-d2-0x20",
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
| `xTool-d2-0x20` | Main MCU (GD32) | `1` |
| `xTool-d2-0x21` | Laser controller | `2` |
| `xTool-d2-0x22` | ESP32-S3 (Wi-Fi/comm) | `3` |

### Single-package check (REST models + D-series)

```
POST https://api.xtool.com/efficacy/v1/package/version/latest
```

```json
{
  "domain": "atomm",
  "region": "en",
  "contentId": "<model_content_id>",
  "deviceId": "<serial>",
  "contentVersion": "<dot-version>"
}
```

Response: a single object with the same shape as one entry of the
multi-package response (or an empty body if no update).

### Cloud content IDs and machine_type per model

| Family / Model | `contentId` | Endpoint |
|---|---|---|
| S1 main MCU + boards | `xTool-d2-firmware`, sub-packages `xTool-d2-0x20` / `xTool-d2-0x21` / `xTool-d2-0x22` | multi |
| D1 | `xTool-d1-firmware` | single |
| D1 Pro | `xTool-d1pro-firmware` | single |
| D1 Pro 2.0 | `xTool-d1pro-firmware-2.0` | single (no firmware uploaded yet) |
| F1 (V1 + WS-V2 firmware lines share the same image) | `xTool-f1-firmware` | single |
| F1 Ultra | `xTool-f1-ultra-firmware-1.5` | single (machine_type `MXF`) |
| F1 Ultra V2 (GS003) | `xTool-f1-ultra-class1-firmware-1.5` | single (machine_type `MXF`) |
| F1 Lite (GS005) | `xTool-f1-lite-firmware` | single (machine_type `MXF`) |
| F2 (GS006) | `xTool-f2-firmware` | single (machine_type `MXF`) |
| F2 Ultra (GS004-CLASS-4) | `xTool-f2-ultra-firmware` | single (machine_type `MXF`) |
| F2 Ultra Single (GS007-CLASS-4) | `xTool-f2-ultra-single-firmware` | single (machine_type `MXF`) |
| F2 Ultra UV (GS009-CLASS-4) | `xTool-f2-ultra-uv-firmware` | single (machine_type `MXF`) |
| M1 | `xTool-m1-firmware` | single (no firmware currently published) |
| M1 Ultra | `xTool-m1-ultra-firmware` | single (machine_type `MLM`) |
| P1 | `xTool-p1-firmware` | single (cloud rejects — kept for ID mapping) |
| P2 | `xTool-p2-firmware` | single (machine_type `MXP`) |
| P2S | `xTool-p2s-firmware` | single (machine_type `MXP`) |
| P3 | `xTool-p3-firmware` | single (machine_type `MXP`) |
| MetalFab (HJ003) | `xTool-hj003-firmware` | single (machine_type `MHJ`) |
| Apparel Printer (DT001) | `xTool-apparelprinter-firmware-1.5` | single (machine_type `MDT`) |
| Bluetooth dongle (peripheral) | `xTool-dongle-firmware` | single |

`xcs-ext-*` IDs that appear in some older XCS resources are XCS
**plugin** packages, not device firmware — the API rejects them with
`资源id不对 / resource id wrong`.

The cloud API has two distinct namespaces selected via the request's
``domain`` field:

| `domain` | ID prefix | Status |
|---|---|---|
| `xcs` | `xcs-*-firmware` | legacy, used by the XCS Android app |
| `atomm` | `xTool-*-firmware` | current, used by the xTool Studio Windows app |

Pick the ``atomm`` namespace + `xTool-*` IDs for current firmware
bundles — only that combination carries the latest builds (e.g.
F1 Ultra `…-firmware-1.5`, D1 Pro 2.0 `…-d1pro-firmware-2.0`). Mixing
prefixes returns ``code 10000 / 资源id不对``.

### Flash flow

Each family has its own wire-level flash sequence. Every step requires
that the response body is validated — HTTP 200 alone is **not**
sufficient on any of these endpoints.

1. **S1** — two-step flash, repeated per board (`xTool-d2-0x20` /
   `0x21` / `0x22`):
   - Download the `.bin` from `contents[].url`.
   - Send `M22 S3` over WS (enter upgrade mode).
   - `POST /upload?filename=<path>&md5=<md5>` — multipart with the
     firmware blob in field `file`. The path matches the XCS / xTool
     Studio `params.filename`:
     - `xTool-d2-0x20` → `update/motion_firmware/mcu_firmware.bin`
     - `xTool-d2-0x21` → `update/laser_firmware/mcu_firmware.bin`
     - `xTool-d2-0x22` → `update/network_firmware/mcu_firmware.bin`
       (older XCS Android used `wifi_firmware`; xTool Studio renamed it).
   - Wait ~3 s.
   - `GET /burn?code=<1|2|3>` — triggers the actual flash from the
     uploaded file. `code` is the burn type (1=main, 2=laser, 3=WiFi).
   - Wait ~3 s, then poll `GET /system?action=get_upgrade_progress`
     until `curr_progress >= total_progress`.
   - Both `/upload` and `/burn` return JSON `{"result":"ok"}` on
     success — anything else means failure.
   - Device reboots on completion.

2. **D-series** (`/upgrade`):
   - Download the `.bin`.
   - `POST /upgrade` multipart with field `firmwareData` carrying the
     raw firmware bytes (xTool Studio Windows). Older XCS Android used
     field `file` with an `application/macbinary` blob type — both
     formats appear to be accepted by D-series firmware, but xTool
     Studio is the current reference.
   - Response body must equal `"OK"` (case-insensitive) or
     `{"result":"OK"}` JSON. An empty 200 body is also accepted.
   - No M22 S3 prelude — the D-series bootloader is entered internally.

3. **REST family — default two-step** (F1, F1 Ultra, F1 Lite, F2,
   F2 Ultra, F2 Ultra Single, F2 Ultra UV, M1 Ultra, MetalFab, P1, P2,
   P2S, P3, Apparel Printer):
   - `GET /upgrade_version?force_upgrade=1[&machine_type=<…>]` on
     port 8087. Response: `{"result":"ok"}`. `machine_type` per
     model: `MXP` for P2/P2S/P3, `MLM` for M1 Ultra, `MXF` for the F1
     family, `MHJ` for MetalFab, `MDT` for Apparel Printer.
   - `POST /package?action=burn` on port 8087 — **raw blob** in the
     request body (no multipart wrapping; matches XCS / xTool Studio).
     `Content-Type: application/octet-stream`. Response:
     `{"result":"ok"}`.

4. **REST family — M1 four-step** (M1 only):
   - The M1 firmware archive returns **two** `contents[]` entries: a
     `.script` payload (small) and a `.bin` blob (the main image).
     Send `.script` first.
   - `POST /upgrade_version` on port 8087 (no `force_upgrade` param).
     Response: `{"result":"ok"}`.
   - `POST /script` on port 8087 — raw `.script` body. Response:
     `{"result":"ok"}`.
   - `POST /package` on port 8087 — raw `.bin` body. Response:
     `{"result":"ok"}`.
   - `POST /burn?reboot=true` on port 8087, empty body. Response:
     `{"result":"ok"}`.

Flashing the wrong image is destructive — bricks the device.
Implementations should require user confirmation and verify the
device's reported `machineType` before invoking any of the flash
sequences above.

---

## Firmware / hardware architecture per family

Each model's firmware archive (`.ht3` / `.ht5` / `.ht8` ZIP for the
Linux-based families, raw `.bin` for ESP32-based D-series) reveals the
hardware split:

| Family | SoC | Sub-MCUs | Notes |
|---|---|---|---|
| S1 | GD32 main | STM32 laser (0x21) + ESP32 WiFi (0x22) | three independent firmware binaries flashed via S1's `/burn` endpoint with `burn_type` 1/2/3 |
| D1 / D1 Pro | ESP32 (single SoC) | — | monolithic ~1 MB firmware blob, OTA via `/upgrade` |
| M1 | Allwinner H3 + Buildroot Linux | GD32 + STM32 motion | tarball with `laserservice` daemon + MCU `.bin`s |
| M1 Ultra | Allwinner R528 (ARM) + Linux | GD450 motion + GD330 Z-axis | adds dedicated Z-axis MCU |
| F1 | Allwinner H3 + Linux | GD450 motion + GD330 purifier | built-in air-purifier firmware |
| F1 Ultra | Allwinner H3 + Linux | display MCU + GD470 motion + GD330 purifier | adds 1 MB display firmware (touchscreen) |
| WS-V2 firmware line | same hardware as the V1 sibling — see [WS-V2 firmware activation thresholds](#ws-v2-firmware-activation-thresholds) for per-model min versions | same | full request/response API on TLS WebSocket port 28900 (replaces port-8080 REST on V2 firmware) |
| P2 | Allwinner H3 + Linux | GD450 motion + GD330 UI + GD330 WCB | UI + cover board MCUs |
| P2S | same as P2 | same | newer revision |
| Bluetooth dongle | dedicated MCU | — | exposes `M9091`–`M9098` for pairing, scan, connect |

