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
| M1 | `40.18.x` | Communication framework upgrade observed on `40.18.026.00.ht3`. Exact rollout threshold not published — Studio bundle exposes the V2 surface (`/v1/parts/control`, `/v1/peripheral/param`, `/v1/platform/accessories`). |
| M1 Ultra | `40.41.017` | Communication framework upgrade. Breaks XCS Mobile. |
| P2 | `40.x` | Studio bundle exposes the V2 surface alongside the legacy REST one. Exact rollout threshold not published. |
| P2S | `40.22.011.06` | Communication framework upgrade. Breaks LightBurn + XCS Mobile. |
| P3 | `40.23.006.03` | Ships V2-only. ⚠️ Update can take 10–15 min. |
| MetalFab (HJ003) | `40.70.013.4` | Studio v1.6+ required. |
| Apparel Printer (DT001) | `40.100.025.03` | Includes manual ink-stack calibration + alignment-reset features. |

V1-firmware lines that have **not** moved to V2 yet: D1 / D1 Pro /
D1 Pro 2.0 (D-series stays on legacy REST + push-WS), P1, S1
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
target choices reach the same firmware; a local broadcast already
covers the LAN scope.

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
- **Two distinct 32-byte AES-256 keys**:

  ```
  primaryKey = "makeblockmakeblockmakeblock-2025"   // outbound encrypt
  commonKey  = "makeblocsdbfjssjkkejqbcsdjfbqlla"   // inbound decrypt
  ```

  Studio's `MulticastServer.encryptData(json, primaryKey)` /
  `decryptData(msg, commonKey)` use them asymmetrically. Encrypting
  the outbound handshake with `commonKey` (or decrypting the response
  with `primaryKey`) yields a packet the device silently drops.

  The body's `data.key` field stays at `commonKey` — that is the key
  the device will use to encrypt its reply. Only the outer AES
  wrapping on the outbound leg uses `primaryKey`.

#### Request payload (plaintext, encrypted with `primaryKey` before send)

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

#### Response payload (decrypted with `commonKey`)

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

#### Deployment caveats

Common LAN-side reasons V2 discovery fails:

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
picker: the user supplies the IP and selects a model + firmware-
generation entry (e.g. ``F2UltraUV`` on V2 firmware) from a
registry-driven dropdown, and the client jumps straight to the
per-protocol handshake (port-28900 TLS WS for V2, REST/8080 for V1,
M-code WS/8081 for S1, …). UDP discovery is a hint, not a hard
requirement.


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
| `M9098` | `M9098 [...]` JSON-ish list | Bluetooth dongle: connected-accessories snapshot. ⚠️ Used by Studio on V2 / REST / D-series via the `/passthrough` tunnel. **S1 firmware does not expose this M-code in a usable shape over raw WS**.

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
| `M9098` | BLE connected snapshot | dongle (V2 / REST / D-series only — S1 firmware doesn't serve this in a usable shape) |
| `M9112` | setBluetoothUnbind | dongle: forget paired device |
| `M9258` | resetFilterWorkTime | reset purifier filter timer |

Studio sends most V3-purifier / duct-fan codes wrapped in the
`uart485` + F0F7 `/passthrough` tunnel (see the BT accessory
subsystem section). **The S1 firmware does not expose
`/passthrough`** — on this family BT-accessory M-codes have to
ride the raw M-code WS directly, and most of the codes below
the firmware silently rejects over that channel. Listed here
for completeness; only `M9039` push frames + the `M1098` slot
array are actually serviceable on S1.

#### Codes verified dangerous — DO NOT SEND

| Code | Effect |
|---|---|
| `M341 S1` | Sends device into `wifi_setup` state (must power-cycle) |
| `M9006 A1` | Crashed WebSocket / forced reboot |
| `M120 A1.1`, `M2810` | Suspicious responses, avoid |
| `M9097` (no MAC) | BLE probe expects ``A<MAC>`` argument — observed to kick the WS / require a reconnect when invoked bare. Only call with a valid paired MAC. |

#### Codes still unverified (do not call blindly)

The following S1 M-codes appear in xTool Studio's bundle but have
not been exercised on a live device. Empirically one of them in
the set kicks the WS — pending bisection. Treat as
"unsafe to call without a controlled retest":

- `M2000`, `M2001`, `M2008` — version / config queries (purpose
  not yet decoded)
- `M9006`, `M9043`, `M9046`, `M9055`, `M9066`, `M9085`,
  `M9091`/`M9092`/`M9093`, `M9112` — accessory / pairing flow
  helpers. ``M9006 A1`` is already flagged as crashing the WS;
  the rest haven't been tested.

#### HTTP probes verified dangerous

- `GET /system?action=<unknown>` (e.g. `list` / `info` /
  `status` / `get_alarm` / `get_dev_info` / `get_machine_info`).
  The WiFi-MCU drops the connection (empty reply) and one of
  these calls was observed to flip S1 into `FIRMWARE_UPDATE`
  status (code 16) for ~6 seconds before it returned to `IDLE`
  on its own. Stick to the four whitelisted actions documented
  in the `/system?action=<name>` table — `version`,
  `socket_conn_num`, `get_upgrade_progress`, `get_dev_name`.

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
| `/parts` (GET) | GET | Always returns `{"result":"ok"}`. POST → `405 "Request method for this URI is not handled by server"`. Stub with no data surface — confirmed on S1 40.32.x. | no |
| `/peripherals` (GET) | GET | Same shape as `/parts` — unconditional `{"result":"ok"}`, no data. | no |
| `/system` (no `action`) | GET | Unconditional `{"result":"ok"}` — only the explicit `action=...` variants above carry data; unknown actions drop the connection (empty reply). | no |
| `/sdcard` | GET | `404 "This URI does not exist"`. The `/sdcard/` prefix is for file-server transfers (`/sdcard/<file>`), not a directory listing. | no |
| `/net/get_ap_list`, `/net/set_wifi`, `/net/setWifi`, `/net/wifi_mode` | GET/POST | Wi-Fi reconfiguration | no (risky) |
| `/dev/console`, `/dev/uart`, `/dev/secondary` | n/a | Internal device file paths | no |

#### Firmware-level structure (decompile)

S1 firmware is a three-binary bundle distributed through xTool's
cloud-update API as `xTool-d2-firmware`:

| Board ID | File | Size | Hardware | Notes |
|---|---|---|---|---|
| `xTool-d2-0x20` | `xtool_d2_gd470_*.bin` | ~430 kB | GD32 Cortex-M (Main MCU) | M-code parser + motion / peripheral state machine |
| `xTool-d2-0x21` | (laser-mcu) | ~30 kB | Laser MCU co-processor | Small set of M-codes (`M115`/`M116`/`M340`/`M1100`-`M1116`/`M1198`-`M1199`/`M98`); firmware-version reporting |
| `xTool-d2-0x22` | `xtool_d2_esp32_s3_app_*.bin` | ~930 kB | ESP32-S3 (Wi-Fi MCU) | Runs the actual HTTP + WebSocket server via ESP-IDF `httpd_ws` |

The Wi-Fi MCU exposes the HTTP routes listed above. The Main MCU
holds the symbolic state-token table that maps `M222 S<n>` codes
to readable names. Tokens visible in the decompile (some still
unmapped to documented semantics):

- Standard work states: `IDLE`, `WORKING`, `FINISH`, `PAUSE`,
  `START`, `SLEEP`, `WORK_READY`, `FRAME_READY`, `FRAME_ONLINE`,
  `PREPARE_DATA`, `PREPARE_STOP`, `TESTING`, `UPGRADE`,
  `MEASURING` / `MEASURE_AREA` (via `BASEPLATE`).
- Errors / faults: `ERROR`, `FLAME_WARNING`, `MACHINE_TILT`,
  `MACHINE_MOVING`, `TROGGER_LIMIT` (sic — firmware typo for
  TRIGGER), `LIGHTBURN` (LightBurn-mode flag).
- Comm-state pushes: `LASER_COMM_STATE`, `WIFI_COMM_STATE`,
  `NETWORK_STATE`, `WIFI_CONFIG` — fired by the Main MCU as
  unsolicited WS frames when the corresponding subsystem changes
  state.
- Capability flags: `FACILITY_SUPPORT_BLE_PURIFIER_V2` — Main
  MCU exposes a capability hint for V2-protocol BT purifiers
  (separate from the older `M9039` push path).
- Mode helpers: `ACTIVE_REPORT` (active-reporting flag),
  the M-code-handler module name, `SYSTEM_MODE_IDLE`,
  `UPGRADE_MODE_STATE_IDLE`, `ERROR_MODE_STATE_IDLE`,
  `TRANSFER_FILE`, `POSITION`, `POWERON`.

#### Studio `/v1/*` envelopes on S1 — Studio doesn't actually send them

xTool Studio's S1 bundle (`/tmp/xtool-exts/S1/index.js`) *contains*
a number of `/v1/*` envelope routes (`/v1/device/configs`,
`/v1/peripheral/param`, `/v1/platform/accessories/list`, the
`/v1/project/*` namespace with `accessory/list`, `accessory/status`,
`accessory/link_status`, `accessory/message`,
`accessory/message_update`, `accessory/upgrade-progress`,
`device/accessory/control`, `api/mcode`).

These routes are gated by `useV2Platform()` which resolves to
`kF.includes(this.channel.deviceCode)` with
`kF = ["JS002", "JS001"]`. S1's `deviceCode` is `MD2`, so
`useV2Platform()` is **always false on S1** and Studio never
actually emits any of these envelopes during an S1 session — they
are shared-bundle scaffolding for the genuinely V2-platform
devices.

Live-probed regardless against S1 firmware 40.32.x — every route
returns `HTTP 404 "This URI does not exist"` across all method
combinations (GET / POST / PUT) and body shapes (`{}`, full
payload, raw M-code text). JSON envelopes of the form
`{"path":"/v1/...","method":"GET"}` sent over the M-code WS
(port 8081) are silently dropped too. The S1 firmware exposes
neither shape.

Implication: every accessory / device-info query on S1 rides the
raw M-code WS or one of the documented HTTP routes (`/cmd`,
`/system?action=...`, `/burn`, `/upgrade`, `/upload`, `/net/...`).

#### Studio "push" mechanics (client-side)

Studio's S1 bundle uses several names that look like firmware
push channels but are mostly internal Electron message-bus
plumbing. Documented here only so an audit doesn't mistake them
for new firmware events:

| Studio name | What it actually does |
|---|---|
| `pushAlarmModal` | Stacks alarm codes into an in-memory error-table for the UI's modal |
| `pushProcessingStatusToMessageCenter` | Renderer-side notification (job finish / disconnect) |
| `notifyFirmwareUpgrade` | IPC into the Electron main process to confirm a firmware upload |
| `broadcastEvent` / `broadcastEventToAllTabs` | Pub-sub between Studio renderer tabs |
| `messageChannel`, `messageType`, `messageUuid` | Identifiers for the in-app message center |
| `SocketReport` / `_handleReportedData` | Real firmware-side push entry point — wraps the unsolicited WS frames (`M222`, `M340`, `M810`, `M9039`, …) |
| `_handleReportedDataForV2Platform` | Alt path used when Studio detects the device speaks V2 envelopes; routes accessory-progress / message-report into the same UI surface |
| `triggerReport` | Studio API verb that forces the firmware to emit a `SocketReport` burst (equivalent of sending `M2211`) |
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

V2 firmware sends and expects every JSON payload wrapped in a
**CRC-16-protected binary envelope** on the WebSocket. Studio's
`MessageEncoder.encodeFrame` / `MessageParser.extractCompletePackets`
are gated by `dataStream: true` — the V2 connection profile sets that
flag, so raw TEXT JSON is silently dropped by the firmware. The
`/v1/user/parity` handshake will never reach the device unless every
frame uses this envelope.

**Envelope layout** (10-byte header + payload):

```
byte 0-1   : 0xBA 0xBE                          frame magic
byte 2-4   : payload length, big-endian (3 B)
byte 5     : protocol_type (low 7 bits) | (CRC enabled ? 0 : 0x80)
byte 6-7   : payload CRC-16/ARC, big-endian
byte 8-9   : header CRC-16/ARC of bytes 0-7, big-endian
byte 10-…  : payload bytes
```

`protocol_type` values from Studio's `ProtocolCode` enum:

| Code | Meaning |
|---|---|
| 1 | F0F7 — legacy M-code framing |
| 2 | F3F4 |
| 3 | F8F9 |
| 4 | JSON — V2 instruction channel uses this |
| 5 | BUFFER |
| 32 | NETWORK_CONFIG |
| 33 | FILE_TRANSFER |
| 34 | MEDIA_STREAM |

CRC-16/ARC: poly 0xA001 (reflected 0x8005), init 0, no xorout — same
algorithm as the `crc` npm package's `crc16` export and Studio's
`crc16_default`.

Reader-side: aggregate inbound BINARY messages into a buffer, scan
for the `0xBA 0xBE` magic, validate the header CRC + payload CRC,
extract the JSON payload. A bad CRC means resync at the next byte —
do not drop the buffer, the device may have split a frame across
multiple WS messages.

Two distinct JSON-payload shapes coexist on the wire:

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
here are the live wire contract — diverging from them causes the
firmware to silently drop every response.

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
| `machine_lock` | USB safety-key presence — `{state: "on"/"off"}` (`on` = key inserted / armed, `off` = key removed / lockout). Studio reads this as `UsbKeyLockStatus`. Not a lid lock. |
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
| `/v1/platform/accessories/list` | GET | Platform accessory list (newer V2-firmware API; replaces `M9098` enumeration). |
| `/v1/platform/accessories/control` | POST `{id:<n>, command:"<M-code>"}` | Higher-level control wrapper (newer V2-firmware API). |
| `/v1/platform/accessories/upgrade` | POST `{params:{id:<type-id>}, data:{filename:<md5>}}` | Trigger accessory firmware flash. Studio two-step flow: (1) upload blob via `file_stream` channel with `params:{fileType:2, fileName:<md5>}`, (2) POST here with the accessory's numeric `Te` type-id + the upload's md5. Device then F0F7-tunnels the firmware to the BT accessory and emits progress via the `accessory.upgradeProgressInfo` push frame. |
| `/v1/platform/device/config` | GET / PUT | Platform device config. |
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

#### Camera capture (WS-V2 still images)

Studio's `captureGlobalImage` (and per-model siblings
`cameraNearSnap`, `cameraUpsideSnap`, etc.) is a sequential 3- or
4-step flow over `instruction` + `file_stream`. The `instruction`
channel returns a filename handle; `file_stream` delivers the
raw JPEG.

| Step | Channel | Method | Path | Body | Purpose |
|---|---|---|---|---|---|
| 1 | `instruction` | GET | `/v1/camera/snap?name=<camera-name>` (or `/v1/camera/image` with `data:{stream:"0"\|"1"\|"near"\|"upside"}` on P2S/P3) | — | Capture frame on device → returns `{filename:"<uuid>"}`. `name` is firmware-specific (`main` / `deep` / `overview` / `closeup` / `fireRecord`); see the per-model table below. |
| 2 | `instruction` | PUT | `/v1/filetransfer/download` | `{filename, fileType:5}` | Initiate blob transfer (`fileType:5` = `CUSTOM`, the camera/log/snap blob class). **F2 Ultra UV firmware 40.130.021 rejects this step with `code -99: error parameters`** — proceed to step 3 anyway; the `file_stream` descriptor still works without the PUT (Studio itself skips step 2 on this firmware). |
| 3 | `file_stream` | n/a | (open fresh WS, send descriptor `{fileType:5, fileName:"<uuid>"}`) | binary frames | Receive raw JPEG bytes; terminate on `{"transferFinish":true}` TEXT or WS close. The same descriptor + the firmware's native MJPEG continuation also drives the live-stream path — one entity per physical lens can serve both still-snap and live MJPEG without separate wire setups. |
| 4 | `instruction` | PUT | `/v1/filetransfer/finish` | `{filename}` | Best-effort end-of-transfer ack — some firmware skips this and closes the WS instead. |

Studio uses this on demand only — there is no Studio-driven
camera-refresh interval. Implementations that want a "live preview"
have to drive their own poll loop. Empirically a 1 Hz cadence is
the lowest the device firmware tolerates without bunching the JPEG
encoder; faster than ~2 Hz starts to drop frames or queue snaps
behind unfinished `file_stream` transfers.

Per-model step-1 variants (audited from each Studio bundle):

| Firmware bundle | Step-1 endpoint | Step-1 query / body |
|---|---|---|
| GS003 (F1 Ultra V2) | `/v1/camera/snap` | `?name=main` (single camera) |
| GS004 / GS006 / GS007 / GS009 (F2 family), HJ003 (MetalFab) | `/v1/camera/snap` | `?name=main` or `?name=deep` (firmware `cameraMediaManager` exposes both — Studio bundles for some F2 models only invoke `main`, but the `deep` selector is accepted by the same code path) |
| P2S | `/v1/camera/image` | body `{stream:"0"\|"1"}` |
| P3 | `/v1/camera/image` | body `{stream:"near"\|"upside"}`, on `port:8329` (HTTP, not WS — V1 fallback) |
| All of the above (V2) | `/v1/camera/snap` | `?name=fireRecord` — captures the buffered frame from the most recent flame-detection event (when supported by the firmware build). |
| F1, M1 Ultra, DT001 | _no camera-snap route in bundle_ | n/a |

#### Camera live video — `media_stream` channel + WebRTC signaling

The third WS channel (`function=media_stream`) carries the live
camera video over **WebRTC**, not a simple WS-MJPEG stream.
Signaling rides the `/v1/signaling/*` and `/v1/platform/camera/*`
endpoints (see below).

**Firmware infrastructure** (`/tmp/f1v2-fw/apps/root/lib/libmk-host.so`,
class `streamService` + `rtc::impl::PeerConnection`):

- The device runs a libdatachannel-based WebRTC peer
  (`rtc::impl::PeerConnection`) that publishes H.264 / H.265 video
  + a SCTP/DTLS data-channel (`application 9 UDP/DTLS/SCTP
  webrtc-datachannel` in the SDP). DataChannel is used for
  control / metadata; the JPEG / video track is published as a
  standard RTC media track.
- `streamService` exposes `addClient(name, comm_base)`,
  `removeClient(name, comm_base)`, `frameCallback`,
  `jpegCallback`, `configCallback` — confirms the firmware can
  serve raw JPEG frames per client over `media_stream` once the
  WebRTC negotiation completes. The WS itself is the signaling +
  data path; raw video is then sent over the negotiated SRTP
  flow.
- Trigger API: `hostApi::call_camera_live` → endpoint
  `/v1/platform/camera/live` (`/v1/platform/*` namespace, see
  below). Failure modes in the firmware string table:
  `"call_camera_live failed, action is not string"`,
  `"action is not valid"`, `"control video failed"`,
  `"streamService not found"`, `"proxyMsgbus not found"`.

**Signaling endpoints** (firmware string table):

| Path | Method | Purpose |
|---|---|---|
| `/v1/signaling/offer` | POST `{id, sdp, deviceID, mid, iceServers}` | Send WebRTC SDP offer + ICE servers list. |
| `/v1/signaling/answer` | POST `{id, sdp}` | Receive SDP answer from the device. |
| `/v1/signaling/candidate` | POST `{id, candidate, deviceID, mid}` | Trickle ICE candidates both directions. |

Failure modes from `hostApi::send_signaling_*`:
`"send_signaling_offer failed, type is not offer"`,
`"send_signaling_offer failed, sdp is empty"`,
`"send_signaling_offer failed, deviceID is not match"`,
`"send_signaling_offer failed, id or description or sdp or
deviceID or iceServers is not found"`,
`"send_signaling_candidate failed, mid is empty"`,
`"send_signaling_candidate failed, id or candidate or deviceID
or mid is not found"`,
`"send_signaling_candidate failed, initService not found"` —
all required fields are mandatory; partial offers are rejected.

**`/v1/platform/*` namespace.** Sits parallel to `/v1/*` on the
same transport (no separate connection / host — the firmware
serves both prefixes on the same TLS WS `instruction` channel).
The namespace mixes two concerns:

- xTool's cloud-account / device-binding SDK (atomm). Endpoints
  like `device/register`, `device/sign`, `device/timestamp`,
  `user/parity`, `user/ping`, `env/domain`, and the
  `/v1/atomm-api/...` mirror unambiguously belong to the
  cloud-account flow.
- Per-feature endpoints (`camera/*`, `accessories/*`,
  `filetransfer/*`, `log`, …) that exist alongside the
  direct-LAN `/v1/*` variants. Studio's V2-firmware dispatch
  uses the `/v1/platform/...` paths as its newer V2 API
  generation, served over the **same** local TLS WS as the
  older `/v1/...` calls.

| Path | Method | Purpose |
|---|---|---|
| `/v1/platform/camera/list` | GET | Enumerate device cameras. |
| `/v1/platform/camera/live` | POST `{action:"start"\|"stop", name:"<camera>"}` | Start / stop the live RTC stream. |
| `/v1/platform/camera/snap` | POST | Snap equivalent of `/v1/camera/snap`. |
| `/v1/platform/camera/calibration/params` | GET | Calibration metadata. |
| `/v1/platform/device/bind` | POST | Bind device → user account. |
| `/v1/platform/device/bind-user` | POST | Reverse bind (user → device). |
| `/v1/platform/device/dev-bind-code` | POST | Issue pairing code. |
| `/v1/platform/device/sign` | POST | Authenticate request signature. |
| `/v1/platform/device/timestamp` | GET | Time-sync for signature freshness (cloud-account flow). |
| `/v1/platform/device/register` | POST | Register device with xTool cloud. |
| `/v1/platform/device/state/sync` | POST | Push runtime state to cloud. |
| `/v1/platform/device/upgrade*` | various | Cloud-mediated firmware update path. |
| `/v1/platform/filetransfer/{upload,download,finish}` | PUT | File transfer (mirrors `/v1/filetransfer/*`). |
| `/v1/platform/log` | GET | Log fetch. |
| `/v1/platform/wifi/{ap-list,connected-info,credentials}` | various | Wi-Fi provisioning. |
| `/v1/platform/env/domain` | GET | Studio backend region hint — returns the URL the Studio desktop app uses to fetch its own runtime config (material database, AP2 filter-life curves, localization, etc.). Device-side it's purely a region resolver; the device itself doesn't consume this URL. |
| `/v1/platform/factory/sign-data` | POST | Factory-signing helper. |
| `/v1/platform/user/{parity,ping}` | various | Account session keep-alive. |
| `/v1/atomm-api/v1/device/{bind-user,dev-bind-code,register,sign,timestamp}` | various | Atomm-namespaced bind + sign endpoints (xTool's internal cloud SDK). |

**Studio's actual usage:** zero. A `grep -c
"RTCPeerConnection\|webrtc\|signaling\|mediasoup\|iceServer"` over
every Studio `index.js` returns `0` everywhere — Studio never
opens `media_stream` in any model bundle. Live preview appears
to be exclusive to the xTool **mobile app**, which is the only
known consumer of `/v1/platform/camera/live` + the
`/v1/signaling/*` exchange. Studio bundles don't exercise it.

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
| `/work/result` | `WORK_RESULT` | `WORK_FINISHED` | Captures `info.timeUse` (job duration in **seconds**, not milliseconds — verified against wall-clock on GS006), `info.taskId`. |
| `/gap/status` | `GAP` | `OPEN`/`CLOSE` | Cover transitions. **Inverted naming:** firmware emits `OPEN` when the cover is closed and `CLOSE` when it is opened (matches the `state:"on"` / `state:"off"` polarity of the polled `gap` peripheral). |
| `/machine_lock/status` | `MACHINE_LOCK` | `OPEN`/`CLOSE` | USB safety-key edge — `OPEN` = key removed (lockout active), `CLOSE` = key inserted (system armed). Matches the `/peripheral/machine_lock` polarity. |

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
| `P_EMERGENCY_STOP` | Emergency-stop button pressed (paired with the `/emergency/status EMERGENCY_STOP VOLTAGE_TRIGGER` push; cleared by `/emergency/status … RESUME`). |

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

### V2 control / state surface

The `instruction` channel covers the full live surface of the device.
A typical client polls this set, with the cadences shown reflecting
the rate at which the underlying values change on the device:

| Endpoint | Method | Typical cadence | Purpose |
|---|---|---|---|
| `/v1/device/runtime-infos` | GET | every tick | `curMode.{mode,subMode,taskId}` → live status |
| `/v1/device/configs` | GET | minute-scale | full persistent config blob |
| `/v1/device/statistics` | GET | minute-to-hour scale | lifetime counters (`working_seconds`, `session_count`, `standby_seconds`, `tool_runtime`) |
| `/v1/processing/progress` | GET | only while status ∈ {`P_WORKING`, framing} | active-job `progress`, `workingTime` |
| `/v1/device/alarms` | GET | every tick | active alarm list |
| `/v1/peripheral/param?type=<X>` | GET | every tick (per type) | live state for one peripheral |

Per-peripheral `type` values (each typically applicable only when the
device model exposes the peripheral):

| `type` | Reads |
|---|---|
| `gap` | Cover state. `state:"on"` = **closed**, `state:"off"` = **open** (V1 REST convention; V2 keeps the same polarity per live MetalFab + F1 Ultra captures). |
| `machine_lock` | USB safety-key presence. `state:"on"` = key inserted (system armed), `state:"off"` = key removed (lockout). Studio's bundle reads this as `UsbKeyLockStatus`. |
| `drawer` | Drawer state. Same polarity as `gap`: `state:"on"` = closed (drawer in slot), `state:"off"` = open / pulled out. |
| `airassistV2` | Air-Assist BLE connect state. **Not exposed on V2 firmware** — HJ003 / GS003 reject the GET with `code -3: error action type !`. |
| `cooling_fan` | Cooling-fan run state. **Not exposed on HJ003** — same `code -3`. |
| `smoking_fan` | Exhaust-fan run state. **Not exposed on HJ003** — same `code -3`. |
| `cpu_fan` | CPU-fan run state. |
| `uv_fire_sensor` | UV flame-detector trip. |
| `water_pump` / `water_line` | Water-loop pump + flow OK. Returns `code 10: device not support` on models without water cooling. |
| `water_tmp` / `water_flow` | Water-loop temperature + flow. Same `code 10` on dry-laser models. |
| `gyro` | Accelerometer X/Y/Z (also `acc_xyz`, `pitch`, `roll`, `yaw`). |
| `laser_head` | Laser-head position X/Y/Z. **Requires `data:{action:"get_coord"}`** — plain GET returns `code 1: failed`. |
| `ir_measure_distance` | Last distance reading. |
| `digital_screen` | Display brightness. |
| `fill_light` | Fill-light brightness (A/B channels, 0-255 native scale — V2 firmware does *not* normalise to 0-100). |
| `ir_led` | IR-LED state. **Requires `data:{index:"global"}`** to read — plain GET returns `code 1: failed`. |
| `ext_purifier` | External-purifier speed + on/off. |

Action paths:

| Path | Method | Body | Purpose |
|---|---|---|---|
| `/v1/device/configs` | PUT | `{kv:{<key>:<value>}}` | Set a single config key (toggles, timeouts, levels, gear). |
| `/v1/device/configs/backup` | GET / PUT | — | Export the full config blob (GET) or apply a previously-exported one (PUT). |
| `/v1/device/configs/restore` | PUT | — | Reset device configs to factory defaults. |
| `/v1/device/operate-log` | GET | — | Documented in Studio's route table (backed by the `/dev/operateRecord` firmware path) but **returns `code -2: invalid request` on F2 Ultra UV firmware 40.130.021** — not exposed on every model. Use with caution. |
| `/v1/device/connect` | PUT | `{action}` | Explicit transport-level connect/disconnect verb. Studio uses it during handover. |
| `/v1/peripheral/control` | PUT | `{type, action, …}` | Actuate a peripheral (turn on/off, set brightness, set speed, home, measure). |
| `/v1/peripherals` | GET | — | List of peripherals currently advertised by the controller. |
| `/v1/device/mode` | PUT | `{mode:"<P_*>"}` | Switch processing mode (`P_PAUSE` / `P_RESUME` / `P_IDLE` / …). |
| `/v1/camera/snap?name=<camera-name>` | GET | — | Single JPEG snapshot. Returns `{filename:"<uuid>"}` over `instruction`; the JPEG arrives on the `file_stream` channel (`fileType:5`). `name` is firmware-specific (`main` / `deep` / `overview` / `closeup` / `fireRecord`). |
| `/v1/camera/image` | GET | — | Legacy P2S/P3 snap variant (body carries `data:{stream:"0"\|"1"}`). |
| `/v1/camera/power` | PUT | `{action:"on"\|"off"}` | Power-cycle the on-board camera. Same surface is also reachable through `/v1/peripheral/control` with `type:"camera_power"`. Camera is on by default after boot — Studio never calls this and `/v1/camera/snap` works without an explicit power-on. Useful for privacy or to reset a wedged stream. |
| `/v1/camera/params` | GET / PUT | — | Camera-wide parameter group (exposure, gain, white-balance — full param surface; live-tuning Studio does not expose). |
| `/v1/camera/fire-record` | GET | — | Most recent flame-detection frame (the snapshot also reachable as `camera_fire_record` via the `?name=fireRecord` snap path). |
| `/v1/camera/fire-record/clear` | PUT | — | Discard the cached flame-detection frame. |
| `/v1/platform/camera/snap` | POST | — | `/v1/platform/*` equivalent of `/v1/camera/snap`. |
| `/v1/laser-head/control` | PUT | `{action, …}` | Laser-head verbs (move, jog, calibrate). |
| `/v1/laser-head/parameter` | GET / PUT | — | Laser-head-wide tuning parameters (power curve, focus offsets, …). |
| `/v1/laser-head/focus/control` | POST | `{action:"start"\|"stop"\|"goTo"\|"auto_start"\|"auto_stop"}` | Autofocus run or explicit Z move. Studio's z-axis-homing button sends `{action:"goTo", autoHome:1, stopFirst:1, Z:0}` to home the Z axis on F2 Ultra UV. **POST only** — PUT returns code 404 on F2 family V2 firmware. |
| `/v1/laser-head/focus/parameter` | GET / PUT | — | Autofocus configuration (search range, step size, dwell). |
| `/v1/motion_control/paramter` | GET / PUT | — | Motion-controller-wide tuning (acceleration, max speed). **Note**: the spelling `paramter` is firmware-canonical (typo in the route table — verified across GS003/GS005/GS006/GS007/HJ003). |
| `/v1/extender/control` | PUT | `{action, …}` | Extender-attachment control (conveyor / rotary table). Paired with the `/conveyor/alarm` push. |
| `/v1/processing/state` | GET | — | Current processing state snapshot (mode + job descriptor). |
| `/v1/processing/progress` | GET | — | Current job progress (`{percent, time_used_s}` — `time_used_s` increments live during a run). |
| `/v1/processing/worktime` | GET | — | Per-job work-time stats. Surface not fully audited; appears to return cumulative durations rather than a live remaining-time estimate. |
| `/v1/processing/type` | GET | — | Job-type discriminator (`engrave`, `cut`, `score`, …). |
| `/v1/processing/batch` | GET / PUT | — | Batch-production mode (run the same job N times). Paired with `/batch/status` push. |
| `/v1/processing/frame/replace` | PUT | — | Swap the framing rectangle mid-job (Studio's "adjust framing" flow). |
| `/v1/processing/upload/config` | PUT | — | Upload a job-config blob to the device (separate from the file-stream payload). |
| `/v1/processing/powerResume` | GET / PUT | `{action:"query"\|"start"}` | Power-loss recovery — query whether a paused job exists from before a power outage, or resume it. |
| `/v1/parts/control` | PUT | `{link, data_b64}` | F0F7-tunnelled M-code to a BT-paired accessory (`M9091`–`M9098`, `M9032`–`M9085`). |
| `/v1/parts/firmware/upgrade` | PUT | — | Push a firmware blob to a paired accessory. |
| `/v1/parts/firmware/upgrade-progress` | GET | — | Poll the in-progress accessory flash. |
| `/v1/file-backups` | GET | — | List the file-backups stored on the device (project storage). |
| `/v1/net/ssid` | GET | — | Currently-joined SSID. |
| `/v1/net/wifi_signal_strength` | GET | — | Current WiFi RSSI as a small integer. |
| `/v1/net/clear-wifi` | PUT | — | Drop stored WiFi credentials. |
| `/v1/wifi/ap-list` | GET | — | Scan result (visible APs). |
| `/v1/wifi/connected-ssid` | GET | — | Same as `/v1/net/ssid` (legacy alias). |
| `/v1/wifi/credentials` | PUT | `{ssid, psk}` | Set new WiFi credentials. |
| `/v1/wifi/interfaces` | GET | — | List network interfaces. |
| `/v1/display/control` | PUT | `{action}` | Front-panel display control (brightness / wake / sleep). |
| `/v1/device/alarms` | GET | — | Currently-active alarm list. |

#### Push events (full table)

In addition to the base events listed above, the `instruction` channel
emits these push frames (all without `transactionId`):

| `url` | `module` | Notes |
|---|---|---|
| `/device/config` | `DEVICE_CONFIG` | `type:"INFO"` — `info` carries a config-blob diff (one or more keys that just changed). Keys observed in the wild: `flameAlarm` (**boolean**; Studio's `handleControlFlame` writes `true`/`false`, not an int enum), `beepEnable`, `gapCheck`, `gapCheckWithKey`, `machineLockCheck`, `autoSleepEnable`, `fillLightBrightFront`, `fillLightBrightBack` (0-255 native), `purifierTimeout`, `purifierSpeed`, `workingMode` (`NORMAL` = stationary / Stops-when-moved enabled, `HANDLE` = handheld override / disabled), `airAssistDelay`, `smokingFanDelay`, `airassistCut`, `airassistGrave`, `sleepTimeout`, `sleepTimeoutOpenGap`, `printToolType`, `fireLevel`, `globalOffsetZ`, `innerZOffset`, `secondOffsetFlag`, `zPositionCompensateSmall`, `ConveyorAngleCompensate`, `ConveyorURate`. |
| `/device/info` | `MACHINE_INFO` | `type:"INFO"` — full machine identity blob (`deviceName`, `sn`, `mac`, `firmware.package_version`, `laserPower[]`, `hardware{}`). MetalFab returns an empty body for the `GET /v1/device/machineInfo`; the same payload arrives via this push a few hundred ms after the WS opens. Consumers should fall back to it when the GET is empty. |
| `/peripheral/<type>` | varies | Per-peripheral push. Observed types: `drawer`, `water_pump`, `water_line`, `cooling_fan`, `smoking_fan`, `cpu_fan`, `uv_fire_sensor`, `ir_led`, `fill_light`, `digital_screen`, `ext_purifier`, `gyro`, `laser_head`, `ir_measure_distance`. |
| `/drawer/status` | `DRAWER` | Drawer transitions. **Note:** the `type` strings invert the obvious meaning — firmware emits `type:"OPEN"` when the drawer is pushed back into the slot, `type:"CLOSE"` when it is pulled out (matches the `state:"on"` / `state:"off"` polarity of the polled `drawer` peripheral). |
| `/emergency_stop/status` | `EMERGENCY_STOP` | `type:"VOLTAGE_TRIGGER"` (e-stop pressed) / `"RESUME"` (released). Emitted by GS002 (F1) / GS003 (F1 Ultra V2) / GS005 (F1 Lite) / GS006 (F2 Ultra UV) and the rest of the F2 family. Newer firmware spelling. |
| `/emergency/status` | `EMERGENCY_STOP` | Older spelling used **only** by HJ003 (MetalFab). Same `type` payload. Pairs with a `/work/mode MODE_CHANGE` push that sets `mode: "P_EMERGENCY_STOP"`. A consumer should subscribe to **both** URL variants. |
| `/board/link` | `BOARDS` | `type:"CONNECT"` — accessory board joined the device (e.g. `info:"weld_machine"`). |
| `/move/status` | `CONTROLLER` | `type:"AXIS_HOME_FINISHED"` — homing per axis (`info:"x"` / `"y"` / `"z"` / `"xy"`). |
| `/laserhead/status` | `LASER_HEAD` | `type:"BUSY"` / `"IDLE"` — laser-head working flag. |
| `/weld/alarm` | `WELD_DEVICE` | MetalFab welding accessory: `type:"AIR_PRESSURE"` (`info` is bar × 100), `"CONNECT"` (`info` = laser power in W), `"DISCONNECT"`. |
| `/button/status` | `BUTTON` | Physical-button event from the device's front panel. Observed `type` strings: `SHORT_PRESS`, `LONG_PRESS`, `DOUBLE_PRESS`. **Watch out** — HJ003 and the F2 family (GS006 / GS007 / GS009) firmware emit `SHOERT_PRESS` (sic) for short presses; consumers should normalise the typo. |
| `/fire/alarm` | `FIRE_RECOGNITION` | Vision-based flame detection (separate from the e-stop / `state.alarm_present` polled field). Use as the canonical trigger for a fire-warning event. |
| `/batch/status` | `BATCH_PRODUCTION` | Progress / state changes in `/v1/processing/batch` runs. |
| `/conveyor/alarm` | `CONVEYOR` | Extender / conveyor attachment errors. |
| `/display/status` | `DISPLAY` | Front-panel display events (brightness change, wake / sleep). |
| `/bluetooth_dongle/alarm` | `BLUETOOTH_DONGLE` | BT-dongle errors (disconnect / pairing failure). |
| `/boards/alarm` | `BOARDS` | Aggregate board-side alarm — distinct from `/board/link CONNECT`. |
| `/camera/alarm` | `CAMERA` | Camera subsystem error (init / restart failure). |
| `/temperature/alarm` | `CONTROLLER` | Over-temperature alarm (controller-board thermistor). `type` values include `TMP_HIGH`, `CUR_HIGH`. Surface as part of the Error Event. |
| `/gyro/alarm` | `GYRO_SENSOR` | Tilt / shock alarm. `type:"MACHINE_TILTED"`. Surface as part of the Error Event. |
| `/laser_head/alarm` | `LASER_HEAD` | Laser-head fault (not the BUSY/IDLE state push above). Surface as part of the Error Event. |
| `/z_axis/alarm` | `MOTOR_DRIVER` / `MOTOR_ALL` | Z-axis motion fault (`ELEMENT_NOT_FOUND`, `ELEMENT_ABNORMAL`, `FIND_EXCEPTION`). |
| `/u_axis/alarm` | `MOTOR_DRIVER` / `MOTOR_ALL` | U-axis (rotary) motion fault — only fires when a rotary attachment is bound. |
| `/machine_lock_for_md/status` | `MACHINE_LOCK_FOR_MD` | MetalFab-specific machine-lock variant (separate from `/machine_lock/status`). |
| `/machine_lock_for_md/alarm` | `MACHINE_LOCK_FOR_MD` | MetalFab machine-lock fault. |
| `/udisk/alarm` | `UDISK` | USB-disk-related fault (insertion-failure / read-failure). |

#### Field-presence guarantees

Many peripheral responses carry only the fields that have a meaningful
value at the moment the response is produced. A consumer should treat
absent fields as "unchanged" rather than clearing the previous value
— several V2 firmware revisions omit numeric fields when their hardware
sensor is currently warming up or unselected.

#### Per-firmware peripheral availability

Not every model accepts every `type` on `/v1/peripheral/param`. Live
captures show the firmware returning one of three error codes when the
type is unsupported:

| Code | Meaning | Behaviour |
|---|---|---|
| `-3` | `error action type !` — type recognised but state-query path missing | Stop polling for the rest of this WS connection |
| `10` | `device not support` — model does not have the hardware | Same |
| `1` | `failed` — generic, often from a `type` that needs a specific `data.action` body | Same (or retry with a known action body) |

E.g. MetalFab (HJ003) rejects `cooling_fan` / `smoking_fan` /
`airassistV2` with `-3` and the water-loop trio with `10`, while the
P-family answers all of them. A V2 client should keep a per-connection
"known-unsupported types" cache and skip the rejected ones on
subsequent polls.

#### Statistics field aliases

`/v1/device/statistics` returns model-specific keys:

| Model family | Keys observed |
|---|---|
| F1 Ultra V2 / GS003 / P3 | `timeModeWorking`, `timeSystemWork`, `numOnlineWorking`, `numOfflineWorking`, `toolRuntime` |
| MetalFab / HJ003 | `clickFlashDrive`, `clickLocalFile`, `fireboxV1_5Used`, `flashDriveGoProcessing`, `insertFlashDrive`, `lastProcessed`, `localFileGoProcessing`, `numOfflineWorking`, `numOnlineWorking` (no time-based counters) |

Consumers should treat the absence of any key as "this firmware
doesn't track that counter" — the corresponding sensor stays
unavailable rather than reporting stale data.


---

## BT accessory subsystem

Cross-family wire reference for the Bluetooth-paired accessories
(IF2 / IF2 2.0 smoke purifier, AP2 air cleaner, cabinet
purifier, AirPump, FireExtinguisher, UV sensor, dongle, …) that
hang off the laser via a UART485-tunneled BLE link.

### Transport

xTool Studio talks to BT accessories through three equivalent
transports — each family exposes at least one:

| Family | Endpoint | Method | Notes |
|---|---|---|---|
| S1 | M-code over WS port 8081 + `M9039` push frames | — | **No F0F7 tunnel.** S1 firmware doesn't serve `/passthrough`; raw WS is the only path. |
| REST V1 family | `http://<host>:8080/passthrough` | POST | F0F7 envelope |
| D-series (D1 / D1 Pro / D1 Pro 2.0) | `http://<host>:8080/passthrough` | POST | F0F7 envelope |
| WS-V2 family | `/v1/parts/control` over the `instruction` WS | POST | F0F7 envelope |

Body shape for the three families that do have an F0F7 tunnel:

```json
{"link": "uart485", "data_b64": "<base64(F0F7-frame)>"}
```

Response carries the F0F7-framed reply under the same
``data_b64`` field.

**S1 is the exception.** It has no `/passthrough` and no
`parts_control` channel — the BT-accessory surface reachable on
S1 is limited to:

- ``M1098`` — fixed-slot firmware-version array of directly-
  wired (USB / serial) accessories (Fire Extinguisher, Air
  Pump, Riser Base, …).
- ``M9039`` push frames — the firmware emits these whenever
  the AP2 air cleaner state changes. The latest snapshot is
  the only AP2 state available on this family.

Air-assist (`M15` / `M1099`) is **not** a BT accessory on S1 —
it's wired to the laser host. On the other families the
equivalent functionality rides the BT tunnel, but on S1 the
laser MCU drives the pump directly via the same M-code WS that
carries the rest of the laser-host commands.

### F0F7 envelope

Mirror of Studio's ``Yt`` (encode) / ``Ft`` (decode) helpers from
the minified bundle. Byte layout:

```
0xF0  prefix(5)  cmd_utf8  0x0A  checksum  0xF7
```

- ``prefix`` is the per-accessory-type discriminator. 5 bytes for
  every supported accessory. ``checksum`` is
  ``sum(prefix + cmd_utf8 + b"\n") & 0x7F``.
- Encoded payload is base64-wrapped before being put into the
  ``data_b64`` JSON field.

Common prefixes from the Studio bundles:

| Type | Prefix bytes |
|---|---|
| Dongle | `[71,115,100,1,0]` ("Gsd") |
| Purifier (cabinet) | `[69,115,96,1,0]` ("Es`") |
| LargePurifier | `[76,115,107,1,0]` ("Lsk") |
| BackpackPurifier | `[84,115,111,1,0]` ("Tso") |
| DuctFan (IF2) | `[70,115,99,1,0]` ("Fsc") |
| DuctFanV3 (IF2 2.0) | `[78,115,99,1,0]` ("Nsc") |
| AirPump / AirPumpV2 | `[70,115,99,1,0]` (shares with DuctFan) |

### Discovery — `M9098 getAllDangleConnectList`

Lists currently-paired accessories on the dongle. Only families
that have an F0F7 tunnel (V2 / REST / D-series) use this M-code.
The reply decodes into ``{type_id, sn/mac, status}`` rows.

**V2 / REST / D-series (CSV variant)** — request goes through
the F0F7 ``/passthrough`` (REST + D-series) or
``/v1/parts/control`` (V2) tunnel:

```
num,mac,type_hex,status;num,mac,type_hex,status;…
```

- ``type_hex`` is the 2-char ``Te.*`` enum value
  (e.g. ``"34"`` = 0x34 = 52 = ``Purifier``).
- ``status`` is ``"1"`` for connected.

The numeric ``type_id_raw`` resolves through the ``Te`` enum
(see below).

**S1 — no `M9098` walk.** S1 firmware doesn't serve the F0F7
tunnel that carries the CSV reply, and the raw-WS variant of
``M9098`` is shaped differently per firmware build with no
stable type discriminator. There is no way to enumerate BT
accessories on S1; AP2 state has to be read from the `M9039`
push frames the firmware emits autonomously, and USB / serial
accessories from the `M1098` slot array (below).

### S1 ``M1098`` — directly-wired (USB / serial) accessories

Distinct from the BT-bound ``M9098`` enumeration: S1's ``M1098``
returns a fixed-position firmware-version array — one slot per
hardwired accessory class the chassis can host. Indexes are
stable across firmware revisions; the per-slot string is empty
when nothing is attached, or the accessory's firmware version
otherwise.

| Slot | Type id | Notes |
|---|---|---|
| 0 | `Purifier` | AP2 air cleaner (also surfaces via the BT path / M9039 push cache — the BT entry wins because of its richer field set) |
| 1 | `FireExtinguisher` | original Fire Extinguisher unit |
| 2 | `AirPump` | Air Pump 1.0 |
| 3 | `AirPumpV2` | Air Pump 2.0 |
| 4 | `FireExtinguisherV1_5` | Fire Extinguisher v1.5 |

A non-empty slot carries the firmware-version string only — no
serial, no per-accessory state fields. Anything richer requires
the M-code surface listed below (which on S1 is largely
unreachable; on the other families it's wrapped in the F0F7
tunnel).

Other families (REST V1 / D-series / WS-V2) don't expose
``M1098`` as a slot array; their per-accessory firmware versions
come back through individual ``M99`` / ``M9097`` info queries
tunneled via the F0F7 path above.

### Per-accessory M-codes

Each accessory advertises a single "info M-code" that returns a
flat state-snapshot line. Writers (gear set, buzzer toggle,
filter reset) ride the same transport with a different M-code.

> Laser-host M-codes — `M15`, `M1099`, `M1100` — do **not**
> travel through the F0F7 tunnel even though they read /
> write what looks like accessory state. They target the
> laser MCU directly over the family's main API (raw WS on
> S1; HTTP `/cmd` on REST / D-series; the `instruction` WS
> on V2). The "AirPump" rows below combine BT-side info
> (`M9082` reply) with laser-host writes (`M15` + `M1099`)
> on families where both apply; S1 only has the laser-host
> half.

| Accessory type | Info M-code | Reply tokens | Writers |
|---|---|---|---|
| `DuctFan` / `DuctFanV3` | `M9082` | DuctFan: ``<v1> <v2> A<gear> C<ctrl> Z<buzzer> E:"<sn>"``. DuctFanV3 (IF2 2.0, verified live on F2 Ultra UV firmware 40.130.021 against a 14-action Studio click trace): ``A<firmware-version-string> B<current_gear> C<c_state> D<mode_class> E:"<sn>" S<buzzer> Z<connected>``. **Field semantics:** `A` = full dotted firmware version (parse positionally, not via `num()`). `B` = current motor speed; in Manual mode this is the gear (1–4); in Manual Off it holds the residual RPM of the prior gear; in Auto modes it reports the ramping speed. `D` = mode_class — authoritative mode discriminator: `2` = Manual Off, `3` = Manual running, `4` = Auto running. `C` = transient state indicator (alternates 2/3 across mode transitions; semantics unclear, ignore unless debugging). `S` = buzzer flag, `Z` = online flag. **Caveat:** `D=4` does NOT distinguish Auto Regular from Auto Quiet — both yield the same poll. Clients tracking sub-mode have to remember it from their own writes (`M9064 B1` = Auto Quiet, `M9064 B3` = Auto Regular) or from M9064 push events; external Studio sets that did not transit the client's write path are unrecoverable from the M9082 reply. | `M9064 <mode><gear>` (mode = `A` Manual / `B` Auto; gear = 0-4 or Auto preset), `M9079 S<0\|1>` (buzzer), `M9085 T<seconds>` (post-run timer), `M9258 A0` (reset filter) |
| `Purifier` / `BigPurifierV3` / `LargePurifier` | `M9033` | ``<v1> <v2> <gear> H<H> I<I> J<J> K<K> L<L> E:"<sn>"`` (H/I/J/K/L = pre / medium / carbon / dense_carbon / hepa filter % per AP2 datasheet) | `M9039 <gear>` (gear), `M9258 0` (reset filter) |
| `BackpackPurifier` | `M9033` | `<vA> H<H> I<I> L<L> E:"<sn>"` (3-filter variant) | `M9258 <filterType>0` (reset filter) |
| `AirPump` / `AirPumpV2` | `M9082` | reuses the DuctFan parser (sn + gear) | — |
| `Dongle` | `M9097` | `<version> E:"<sn>"` | — |
| `FireExtinguisher` / `SafetyFireBoxPro` / `UvSensor` / `MultiFunctionalBase` / `Feeder` / `HotStampingPen` / `UltrasonicKnife` | (stub) | sn + version only | — |

Filter wear semantics: the AP2 datasheet names the cabinet
purifier's 5 filters
``pre`` / ``medium`` / ``carbon`` / ``dense_carbon`` / ``hepa``.
Studio's anonymous tokens H/I/J/K/L map onto these names in
order.

### `/accessory/status` push (V2 only)

The WS-V2 ``instruction`` channel emits an ``/accessory/status``
push event whenever a paired accessory's gear, buzzer, or filter-
wear state changes. Observed shape on F2 Ultra UV (GS006) with an
IF2 2.0 paired:

```
{
  "url": "/accessory/status",
  "module": "DEVID_MCODE",
  "type": "VALUE_CHANGE",
  "info": { "mcode": "M9064 A1 B3 C4 D0 S0" }
}
```

``info.mcode`` carries the full M-code body of the accessory's
info reply (same shape Studio polls via the F0F7 tunnel — see
"Per-accessory M-codes" below). Consumers route by the M-code
opcode (e.g. ``M9064`` → DuctFan / DuctFanV3, ``M9039`` →
Purifier family) and merge the parsed fields into the paired
accessory's cached state without waiting on the next BT walk.

**DuctFanV3 ``M9064`` push body** uses the same letter-positional
convention as the M9082 reply but **without** the firmware-version
anchor: ``A<a> B<b> C<c> D<d> S<s>``. ``D`` carries the
authoritative ``mode_class`` (same semantics as the poll: 2/3/4
for Manual Off / Manual running / Auto running). ``A`` echoes the
last manual gear (0 = Off, 1–4) and is useful as an immediate
``current_gear`` hint when ``D=3`` so the consumer entity can
flip before the next M9082 poll (~600 ms behind). ``B`` and ``C``
are transient state indicators; their values do **not** reliably
discriminate Auto Regular from Auto Quiet — sub-mode tracking
needs to be done client-side from the writer path (`M9064 B1` =
Quiet, `M9064 B3` = Regular). ``S`` mirrors the buzzer flag.

### `M9098` reply shape per family

Two shapes are observed in the wild. Both ride the same F0F7
tunnel and a parser has to auto-detect them per row:

| Family | Carrier | Reply tokens |
|---|---|---|
| WS-V2 (older firmware: F1 Ultra V2) | `/v1/parts/control` (F0F7) | V2 ``A<type> B<status> E:"<sn>"`` tokens |
| WS-V2 (newer firmware: F2 Ultra UV, …) | `/v1/parts/control` (F0F7) | CSV variant ``num,mac,type_hex,status;`` per row (see [Discovery](#discovery--m9098-getalldangleconnectlist) above) |
| REST V1 | `/passthrough` port 8080 (F0F7) | V2 token shape (firmware mirrors REST and V2); newer V1 builds may also emit the CSV variant |
| D-series | `/passthrough` port 8080 (F0F7) | V2 token shape |
| S1 | — | No usable `M9098`. AP2 derives from `M9039` push frames; USB/serial accessories from `M1098`. |

The CSV row's ``type_hex`` is the first byte of the
``Te.F0F7``-prefix array (the same enum used by the token
shape — table below); the remaining hex chars are the rest of
the prefix bytes (e.g. ``4E736300`` → ``0x4E`` = 78 =
``DuctFanV3``, followed by ``Nsc\0``).

### `Te` enum (numeric type-id mapping)

The 2-char hex token in the `M9098` CSV identifies the
accessory type. Only the WS-V2 / REST / D-series Studio
bundles use this — they all share the ``Te.*`` enum:

| Hex | Type id |
|---|---|
| 0x32 (50) | FireExtinguisherV1_5 |
| 0x34 (52) | Purifier |
| 0x3D (61) | AirPump |
| 0x40 (64) | AirPumpV2 |
| 0x46 (70) | DuctFan |
| 0x4A (74) | Dongle |
| 0x4B (75) | UvSensor |
| 0x4C (76) | LargePurifierV3 (AP2 Max) |
| 0x4E (78) | DuctFanV3 |
| 0x52 (82) | SafetyFireBoxPro |
| 0x53 (83) | MultiFunctionalBase |
| 0x54 (84) | BackpackPurifier |

Unknown ids are logged once and skipped; the registry extends
as accessories with live logs land in the issue tracker.

### Brand-name mapping (Studio `bF` table)

Studio's `bF` firmware-id → marketing-name dict:

| Type id | Brand label |
|---|---|
| `DuctFan` | xTool SafetyPro IF2 |
| `DuctFanV3` | xTool SafetyPro IF2 2.0 |
| `Purifier` | xTool SafetyPro AP2 |
| `LargePurifier` | xTool SafetyPro AP2 (Large) |
| `LargePurifierV3` | xTool SafetyPro AP2 Max |
| `BackpackPurifier` | xTool Backpack Purifier |
| `AirPump` | xTool Smart Air Assist |
| `AirPumpV2` | xTool Air-Compress Assist |
| `FireExtinguisher` | xTool Fire Safety Set |
| `FireExtinguisherV1_5` | xTool Fire Safety Set v1.5 |
| `SafetyFireBoxPro` | xTool SafetyFireBoxPro |
| `UvSensor` | xTool Firesense Hub |
| `Dongle` | xTool Bluetooth Dongle |
| `MultiFunctionalBase` | xTool MultiFunctional Base |
| `Feeder` | xTool Feeder |
| `HotStampingPen` | xTool Hot Stamping Pen |
| `UltrasonicKnife` | xTool Ultrasonic Knife |

### Semantics gotchas

**M15 air-assist flags (laser host).** `M15 A<n> S<gear>`
carries two independent fields:

- `A` — accessory plug state. `A=1` means the air-assist pump
  is wired up to the laser; `A=0` means no hardware detected.
  Raw connectivity flag only.
- `S` — commanded gear (0 … N). `S=0` means the pump is idle
  even if `A=1`.

Air is actually flowing only when both `A=1` **and** `S>0`.
Watching `A` alone reports "running" the moment the hardware
is plugged in.

**`M1098` carries no serial numbers.** Slot index is the only
stable per-accessory discriminator. Anything richer (firmware
version, gear, filter wear, …) requires the M-code surface
above — which on S1 is largely unreachable.

**Cross-variant reply shapes.** The cabinet `Purifier` and the
S1 AP2 share `M9033` as their info M-code but their replies
diverge: the AP2 push frame carries the running flag plus the
two `purifier_sensor_d` / `purifier_sensor_s` particle
counters; a plain cabinet `Purifier` reply does not. Field-
presence (rather than accessory type) is the reliable
discriminator.


---

## REST API family (F1 / F1 Ultra / F1 Ultra V2 / F1 Lite / F2 / F2 Ultra / F2 Ultra Single / F2 Ultra UV / M1 / M1 Ultra / MetalFab / P1 / P2 / P2S / P3 / Apparel Printer)

JSON over HTTP. Verified against the per-model `index.js` bundles in the XCS APK and the newer xTool Studio Windows app (`exts.zip/<model>/index.js`).

### M1 dialect — mixed text/JSON shapes

The original M1 uses a distinct V1 REST surface (separate from the F-series, P-series and M1 Ultra "modern" V1 dialect). Its device-identity bootstrap stitches **multiple endpoints with mixed response shapes** — some return bare text, some JSON.

| Endpoint | Return | Field used |
|---|---|---|
| `GET /system?action=get_dev_name` | plain string | device name |
| `GET /getmachinetype` | plain string | serial / machine-type code |
| `GET /getlaserpowertype` | JSON `{result:"<W>"}` | laser power (W) |
| `GET /system?action=version_v2` | JSON `{package_version, master_h3_laserservice, …}` | firmware version |
| `GET /net?action=ifconfig&t=<ms>` | JSON `{mac, wlan0-ip, eth0-0-ip, …}` | MAC + IPs |

Other V1 models expose a single `GET /device/machineInfo` endpoint that returns the full identity blob as one JSON object. M1 doesn't implement that path — hitting it returns an empty body / non-JSON. Clients calling `/device/machineInfo` against M1 will see `Expecting value: line 1 column 1` parse errors.

The M1 also uses different action paths for job control (`/cnc/cmd?cmd=<M-code>` for homing/light/lock, `/cnc/data?action=start|pause|stop` for processing) and fill-light (`/setfilllight?bright=<n>` GET-with-query instead of POST-body). The same legacy dialect applies to P1 (oldest Laserbox firmware).

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
| `/peripheral/fill_light` | POST `{action:"set_bri",idx,value}` | Fill light brightness. **Note:** on F2 family V2 firmware this endpoint accepts the PUT but never persists the value — Studio writes brightness through `/v1/device/configs` with `fillLightBrightFront` / `fillLightBrightBack` keys instead. The peripheral endpoint is read-only on those models. |
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

