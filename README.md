# xTool

Home Assistant integration for xTool laser cutters, engravers, fiber-laser welders and inkjet / UV printers (S1, D1 family, F1 family, F2 family, M1 family, M2, P family, MetalFab, Apparel Printer (Plus), O1 Omni, Retail Marker, X1, …). It talks to the device on your local network; no cloud and no xTool account are needed.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/thecodingdad/ha-xtool)](https://github.com/thecodingdad/ha-xtool/releases)

> [!IMPORTANT]
> **This is an independent community project and is not affiliated with or endorsed by xTool. The integration is provided as-is, without any warranty.** Operating a laser cutter, engraver or fiber-laser welder through Home Assistant carries real-world safety risks (fire, blindness, burns, electrical hazards) — automated control over a high-power laser is *your* responsibility. Use at your own risk; see the full [Disclaimer](#disclaimer) for details.

> [!NOTE]
> **Developed with an xTool S1.** The other models follow what xTool Studio does with them. Several have been tested by users on their own lasers, others not yet: feedback and debug logs are welcome.

## Features

- Device status, job control (pause, resume, cancel, homing) and job, error and fire events for automations
- Fill lights, buzzer, fans, safety settings, cameras and sensors (position, temperatures, lifetime statistics, …)
- Bluetooth and wired accessories as their own devices (Smart Air Assist, SafetyPro AP2 / AP2 Max, SafetyPro IF2 / IF2 2.0, Fire Safety Set (2), …)
- Firmware update entity with the release notes from xTool's cloud (installing is opt-in)
- Optional power switch (smart plug) with automatic power-off
- Model and firmware detected automatically; follows the laser when its address changes

## Prerequisites

- Home Assistant 2025.10.0 or newer
- xTool device on the same local network

## Supported Devices

| Family | Models |
|--------|--------|
| S1 | S1 |
| D series | D1, D1 Pro, D1 Pro 2.0 |
| V1 firmware | F1, F1 Ultra, F1 Ultra V2 (GS003), F1 Lite (GS005), F2, F2 Ultra, F2 Ultra Single, F2 Ultra UV, M1, M1 Ultra, MetalFab (HJ003), P1, P2, P2S, P3, Apparel Printer (DT001), Apparel Printer Plus (DT003) |
| V2 firmware | F1, F1 Ultra, F1 Ultra V2 (GS003), F1 Lite (GS005), F2 family, F2 Ultra UV Class 1 (GS009-CLASS-1), Retail Marker (GS008), M1 Ultra, M2 (JS002), P2S, P3, MetalFab, Apparel Printer, Apparel Printer Plus (DT003), O1 Omni (DT002), X1 (JS001) |

Whether a laser runs V1 or V2 firmware is detected automatically. O1 Omni (DT002) support is read-only for now: status, consumables, doors, environment, print queue, alarms, job events and the firmware-update check.

**X1 (JS001).** Pairs with a button press (see [Pairing](#pairing-newer-v2-firmware)): the X1 waits only about 15 seconds for it, so start the setup standing at the laser. Checked on an X1 with firmware 40.140.003.01.ht18; job events and the job buttons follow Studio but have not run during a job yet. The X1 shows a connected SafetyPro AP2 and Fire Safety Set 2 as their own devices: the AP2 with its filters, Air cleaner running and a Purifier level select (Off, 1-4 or Auto, as Studio sets it; Auto lets it follow the laser), the Fire Safety Set 2 read only. A fire alarm of the X1 fires the Fire warning event. With **Enable firmware updates** on, new firmware for the X1 and for its AP2 and Fire Safety Set 2 is installed as Studio installs it (the X1 restarts to install its own; the update finishes once it is back and idle). Checked on an X1: 40.140.003.01.ht18 to 40.140.003.21 (a 350 MB package) took about 6 minutes from Home Assistant. Accessory firmware has not been installed yet: none newer has been released for the AP2 or the Fire Safety Set 2.

## Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=thecodingdad&repository=ha-xtool&category=integration)

Or add manually:
1. Open HACS in your Home Assistant instance
2. Click the three dots in the top right corner and select **Custom repositories**
3. Enter `https://github.com/thecodingdad/ha-xtool` and select **Integration** as the category
4. Click **Add**, then search for "xTool" and download it
5. Restart Home Assistant

### Manual Installation

1. Download the latest release from [GitHub Releases](https://github.com/thecodingdad/ha-xtool/releases)
2. Copy the `custom_components/xtool` folder to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

## Configuration

### Adding a Device

1. Go to **Settings** > **Devices & Services** > **Add Integration**
2. Search for **xTool**
3. Select a discovered device, or choose **Enter IP address manually**
4. When the model cannot be detected, pick it from the list

**Address changes.** The integration follows a laser to a new address: when Home Assistant sees it ask the router for one (DHCP), or, when a laser that is on stops answering, by searching for its serial number as xTool Studio does. A P2 uses its Ethernet address when it has one.

### Pairing (newer V2 firmware)

Some V2 firmware (first seen on the M2 with `40.141.012.01.ht05`) only accepts paired clients. Setup then shows a pairing step: click **Submit**, then press the button on the device. If the device later rejects the pairing (e.g. after a reset), Home Assistant asks for **re-authentication**; press the button again. Your device, entities and history stay as they are. **Reconfigure** on the integration entry changes the IP address or pairs again.

### Options

**Settings** > **Devices & Services** > **xTool** > **Configure**. Only the options that apply to the model are shown.

| Option | Description |
|--------|-------------|
| Power switch | A switch (e.g. a smart plug) that powers the laser. While it is off, Status shows "Off" and the other entities are unavailable; only the lifetime totals keep their value. After it turns on, Status shows "Initializing" until the laser answers (at most 3 minutes), then "Not responding" while the laser does not answer |
| Polling interval | How often the laser is read, in seconds (default 5) |
| Enable firmware updates | Off by default: the update entity only reports new firmware. On: it can install it |
| Firmware-update check interval | How often xTool's cloud is asked for new firmware (default 6 h) |
| Overlay colors | **P2.** The colors of the camera overlays |
| AP2 air cleaner | **S1.** Adds the AP2 air-cleaner entities. Leave off without an AP2 |
| AP2 air-cleaner polling interval | **S1.** Default 30 s |
| Lifetime-stats polling interval | **S1.** Working time, session count, standby time, tool runtime. Default 300 s |
| Accessory polling interval | **S1.** Wired accessories such as the Air Pump and Fire Extinguisher. Default 60 s |

## Entities

Only the entities the model and its connected accessories support are created.

**Entity IDs** have the form `<platform>.xtool_<model>_<serial>_<key>` (for example `sensor.xtool_s1_mxdk101001241104ebb4cc3_status`), whatever language Home Assistant uses. Entities created by an earlier version keep their entity IDs.

### Sensor

| Entity | Description |
|---|---|
| Status | Off / Idle / Processing / Paused / Cleaning / Finished / Error / … Always available: "Off" when the laser cannot be reached, "Not responding" when a linked Power switch is on but the laser does not answer. **F2 Ultra UV** also: Focusing, Warming up, Laser overheat. **P3** also: Busy (while xTool Studio works with its cameras) |
| Task ID | Current or last job |
| Task time | Elapsed time of the current or last job |
| Last job time | Duration of the last finished job (**F2 Ultra UV**: kept across restarts) |
| Working mode | Cut / engrave / knife / inkjet / … (V1 firmware, D series) |
| Working time / Session count / Standby time / Tool runtime | Lifetime totals |
| Laser position X / Y / Z | Laser-head position in mm (**X1**: X / Y) |
| Fire level | Flame-detection level |
| Air-Assist level | Air-Assist gear (0-4) |
| Workhead / Workhead Z height | Fitted workhead and its Z offset |
| Workhead / Basic carriage / Blade | **M1 Ultra.** The quick-swap module (10W / 20W laser module, multi-function carriage, inkjet module), the tool in the basic carriage (fine-point pen, fine-point blade, hot foil pen) and, with the multi-function carriage fitted, its blade (cutting blade, rotary blade, foil transfer tip; *none* until synced with Sync blade, unavailable without the multi-function carriage). Read while the laser is awake. |
| Z-axis temperature / Water temperature / Water flow rate / Gyro X / Y / Z | As reported by the laser |
| Print tool type / Hardware type | As reported by the laser (not on the M1 Ultra with V2 firmware, which does not report the tool type or tool runtime) |
| AP2 filters / AP2 sensor D / S / Purifier speed | AP2 air cleaner: filter remaining, particle sensors, speed |
| Ink level cyan / magenta / yellow / black | **M2.** Remaining ink |
| *&lt;cartridge&gt;* ink / Moisturizing fluid 1 / 2 | **O1 Omni.** Remaining % per cartridge and tank |
| Print head 1 / 2 temperature / Job name / Job progress / Job time remaining / Queued jobs | **O1 Omni** |
| Task progress | **M1, P2.** Percent of the running job |
| CPU temperature | **M1, P2.** Diagnostic, off until turned on |
| Active alarms | **O1 Omni.** Number of alarms; the `alarms` attribute lists them as in xTool Studio |
| UV lamp runtime / Air purifier runtime | **O1 Omni.** Lifetime totals |
| Ambient temperature / humidity | **Apparel Printer, O1 Omni** |
| Laser backpack | **X1.** None, 30 W fiber, 60 W MOPA, 5 W UV or 5 W UV + 60 W MOPA (diagnostic) |
| Engraving lens | **F2 Ultra UV.** Surface, Inner or None |

**Diagnostic:** IP address, Laser power, Laser module, SD card, Workspace size, Active connections (clients connected to the laser, such as XCS), Origin offset X / Y, Last distance (IR measurement), Riser base (**S1**), Inkjet head serial / firmware and toner serial (**M2**), Wi-Fi signal (**F2 Ultra UV, X1**; **M2**: also Wi-Fi network and channel, all three off until turned on), and on the **F2 Ultra UV** Connection (Ethernet or Wi-Fi) and Calibration (when Studio last calibrated the camera; the stored offsets as attributes, never changed).

### Binary Sensor

| Entity | Description |
|---|---|
| Cover / Drawer / Front door / Rear door | Open or closed (doors: **O1 Omni**) |
| Safety key | The access-control key is plugged in |
| Air-Assist running / connected | Air is flowing / the Air-Assist is plugged in |
| Cooling fan / Exhaust fan / CPU fan / Water pump | Running |
| Water line / UV fire sensor | Water flow OK / UV flame detector tripped |
| Exhaust fan installation | **F2 Ultra UV.** Problem when the laser reports no exhaust fan (Studio warns before a job) |
| Flame alarm / Beep / Gap check / Lock check enabled | The laser's settings |
| Alarm | Any active alarm |
| XCS compatibility mode | On while the XCS desktop app holds the laser's connection |
| Waste ink tank full / installed, Air filter installed | **O1 Omni** |
| Feeder / Feeder front sensor / Feeder rear sensor | **X1.** The conveyor feeder is fitted; material is at its front or rear sensor |
| Diode laser module / Inkjet module | **X1.** The module is fitted (diagnostic) |
| Inkjet head capped / Toner installed / Inkjet head calibrated | **M2** |

Bluetooth accessories (Smart Air Assist, SafetyPro AP2 / AP2 Max, SafetyPro IF2 / IF2 2.0, Fire Safety Set, …) are devices of their own, connected to the laser, with their own entities: gear, running, filter wear, connection, post-run time, buzzer and so on.

The **Fire Safety Set 2** shows Extinguishing (on while it sprays), Gas cylinder, Gas level (0-3, as Studio shows it), the Problem sensors Gas reserve, Extinguisher drive and Cylinder calibration, and Gas remaining (diagnostic, the raw value Studio reads).

#### SafetyPro IF2 2.0 fan

One fan entity: Off, Manual speeds 1-4 and the presets **Auto Regular** and **Auto Quiet**. In Auto the IF2 runs from the start of a job until its post-run time ends, at the speed the job carries (Studio writes 3 for Auto Regular and 2 for Auto Quiet). The IF2 reports only the speed, so speeds 1-2 show as Auto Quiet and 3-4 as Auto Regular. During a job, a preset changes the speed; turning the fan on or off or setting a speed switches the IF2 to Manual. A preset chosen while no job runs only stores its speed. The **Alert** event entity fires **Fan stall** or **Cleaning needed** when the IF2 reports them.

### Switch

| Entity | Description |
|---|---|
| Power | The Power switch from the options |
| Buzzer / Beep | Sounds |
| Move stop / Tilt stop / Limit stop | Stop on movement, tilt or limit |
| Stops when moved | **V2 firmware.** A job stops when the laser is moved |
| Stops when enclosure opened | A job pauses when the lid opens |
| Exhaust fan / Cooling fan | Fans. The **F2 Ultra UV** does not report its exhaust fan, so the switch shows what Home Assistant set last |
| Purifier check / Drawer check | A job needs the accessory or a closed drawer |
| Purifier auto-continue | Purifier keeps running after a job |
| Red dot / Red dot close | Red-dot pointer and, on V1 firmware dual-camera models, the close camera's IR LED |
| Cover lock | Locks the cover |
| Flame alarm | **Warning (per xTool Studio):** with it off, no flame alarm is triggered. Disabled by default. **F2 Ultra UV** attribute `flame_record`: a photo of a fire alarm is stored |
| Access control | As xTool Studio: each job needs the access-control check at the machine, and autofocus, framing and offline processing are off. Only on the units Studio offers it on, see below. Disabled by default |
| Device sleep | **V2 firmware, F1 / F2 family.** The laser sleeps when idle (xTool recommends keeping it on) |
| Smart trial run check | **X1.** Before a job with the 20 W diode, 30 W fiber or 60 W MOPA module, the X1 checks for anomalies first (Studio's setting) |

#### Access control

Access control is the Safety Enhancement Mode of xTool's Access Control Key, for the EU Machinery Directive 2006/42/EC, offered only on units sold in the EU ([xTool: Access-Control Key](https://support.xtool.com/article/1367)). As in xTool Studio, the switch is added only when the SKU the laser reports (the Model ID on the device page) starts with one of these:

| Model | SKUs |
|---|---|
| F2 Ultra UV | P0101000396, P0101000397 |
| F2 Ultra | P0101000661, P0101000659 |
| F2 Ultra (Single) | P0101000665, P0101000664 |
| F2 | P0101000600, P0101000601 |
| F1 Ultra | P0101000604, P0101000599 |
| P3 | Machine style `lowCost_europe` |

The F2 Ultra UV (Class 1) has it on every unit. Until the laser has reported its SKU, the switch is shown.

### Number

| Entity | Description |
|---|---|
| Auto power-off | With a Power switch only. Turns it off after the laser has been Idle or Sleeping for this many minutes (0 = never, up to 1440). Anything else (a job, framing, going offline) or a new value restarts the count |
| Air-assist post-run / Exhaust post-run | How long it runs after a job |
| Sleep timeout / Sleep timeout (cover open) | Idle time before the laser sleeps |
| IR-light auto-off / Purifier auto-off | Auto-off delays |
| Camera exposure | Camera exposure |
| Camera thumbnail refresh interval | **V2 firmware cameras.** How often the picture on the device page refreshes (5 s to 10 min); each refresh takes a photo for every open screen showing it |
| Crop width / height / center X / center Y | **F2 Ultra UV** main camera: the part of the work surface shown, in mm (default: the calibrated 200 × 200 mm; 0 = as wide as the photo reaches) |
| Air-Assist gear (cut / engrave) | Default gear for the next job (0-4) |
| Display brightness | Touchscreen brightness |
| Tilt threshold / Movement threshold | Sensor sensitivities |
| Work area limit (left / right / up / down) | Work-area limits in mm |
| Focus height | **F2 Ultra UV.** The material thickness the laser is focused on (0-150 mm). Setting it moves the head like Studio's manual focus; refused during a job, while focusing or measuring, on an error, and with the inner engraving lens |

#### Action `xtool.camera`

Sets the crop of a straightened main camera (**F2 Ultra UV**) and refreshes its picture. Give any of `crop_width`, `crop_height`, `crop_center_x` and `crop_center_y` in mm; the others stay.

```yaml
action: xtool.camera
target:
  entity_id: camera.xtool_f2ultrauv_<serial>_camera_main
data:
  crop_width: 267
  crop_height: 200
```

#### Action `xtool.feeder_clamp`

Clamps the material in an X1's conveyor feeder for its `thickness` in mm (0-68.9) and `material` (`rigid`, the default, or `flexible`), as Studio does when a thickness is entered in feeder mode. Refused unless a feeder is fitted and the laser is idle; **Release material** lets the material go again.

```yaml
action: xtool.feeder_clamp
data:
  device_id: <the X1's device>
  thickness: 3
  material: rigid
```

#### Action `xtool.focus`

Moves the focus of a laser with a Focus height entity: a signed `step` in mm (positive moves up), optionally repeated `steps` times, or a `thickness` to move to. A move stops at 0 or 150 mm and then reports an error that says so. The response has `focus_height`, the height it moved to.

```yaml
action: xtool.focus
target:
  entity_id: number.xtool_f2ultrauv_<serial>_focus_height
data:
  step: -0.5
  steps: 2
```

### Select

| Entity | Description |
|---|---|
| Purifier speed | Off / Low / Medium / High |
| Flame alarm mode | Flame-detection mode 1-4 |
| Flame alarm sensitivity | **X1.** Low or High, as Studio's setting |
| Exhaust fan speed | **X1.** 75, 90 or 100 %, as Studio's setting |
| Red-cross mode | Cross-laser pointer / Low-light mode |
| Perspective | **F2 Ultra UV** deep camera: Uncorrected, Rotated (upright) or Straightened (default: upright and seen square from above) |

### Light

| Entity | Description |
|---|---|
| Fill light | Dimmable work light (0-100 %) |
| Fill light front / Fill light back | **F2 family.** The two fill lights, each on its own |
| Fill light left / right / close | **X1.** The three lights Studio uses for photos, each on its own. The close light goes up to 50 %, as in Studio |

### Camera

On a laser with several cameras, each camera and its settings have their own device, connected to the laser. Cameras on V2 firmware have no live video: their live view is built from photos (about 2.5 per second), and their state is Streaming while one is open. The cameras of the **X1** and the **P3** (V2 firmware) have: they offer Home Assistant's live stream (HLS or WebRTC, through its Stream integration and go2rtc) of the laser's own video, passed on without decoding, from one camera at a time as in Studio: opening another camera's live view switches the video to it, and the camera it came from stops its stream until it is opened again. Their pictures still come from photos. The **P3** has, as in Studio, an Overview camera (live; its picture is a frame of the live video, as the P3 takes no plain photo of it), a Side camera (live and photos) and a Close camera (photos only). The **M1** has one camera, with photos as Studio takes them.

| Entity | Description |
|---|---|
| Camera | Workspace camera of single-camera models (e.g. F1 Ultra V2) |
| Main camera / Deep camera | **F2 family, MetalFab.** Workspace and close-up camera. On the **F2 Ultra UV** the main camera is straightened with the laser's calibration and the deep camera turned upright (see Perspective) |
| Overview camera / Close camera | **P family, V1 firmware dual-camera models.** On the **P2** straightened, with crop presets and overlays, see [P2 cameras](#p2-cameras) |
| Global / Local / Side camera | **M2** (**X1**: Global and Local, the two cameras Studio takes photos with) |
| Flame record | Photo of the last flame alarm |
| Calibration (image) | **F2 Ultra UV** (diagnostic): the main camera photo with Studio's calibration grid |

#### P2 cameras

The P2's cameras are straightened with its own calibration. Each has Crop presets, overlay switches (also as attributes, for dashboard cards drawing their own), an Overlays select and an Exposure; the overview picture follows Mode and Thickness or distance, which **Measure thickness or distance** can fill in. Laser position X / Y with **Move laser** or the `xtool.move` action move the laser. [docs/P2.md](docs/P2.md) has the details, and the P2 entities left out because Studio does not offer them.

### Button

| Entity | Description |
|---|---|
| Pause / Resume job / Cancel | Job control |
| Home all axes / Home XY / Home Z / Home laser / Z-axis homing | Homing |
| Measure distance | IR distance measurement |
| Sync blade | **M1 Ultra.** Turns the multi-function carriage so it identifies its blade (shown in Blade); refused until the axes are homed. Only while the multi-function carriage is fitted |
| Autofocus | **F2 Ultra UV.** As Studio's Autofocus; Focus height shows the result |
| Wake | **P2.** Wakes a sleeping P2 so its cameras take photos again |
| Reconnect Bluetooth accessories | **X1.** Available while a paired Bluetooth accessory (the SafetyPro AP2) is not connected. Searches for it for up to 2 minutes and connects it, as Studio's accessory window does. An AP2 that lost its pairing shows up only in pairing mode: hold its power button for 5 seconds until the Bluetooth light blinks |
| Reboot / Sync time / Quit LightBurn | Restart, set the laser's clock, leave LightBurn standby |
| Release material | **X1.** The conveyor feeder lets go of the material, as Studio's button. Only with a feeder fitted and the laser idle |

### Update

| Entity | Description |
|---|---|
| Firmware | Installed and latest firmware from xTool's cloud, with its release notes. Installing needs **Enable firmware updates** in the options |

### Event

Events fire once on each change, for automation triggers.

| Entity | Event types | Description |
|---|---|---|
| Button | `short_press`, `long_press`, `double_press` | The laser's own button |
| Job | `started`, `paused`, `resumed`, `cancelled`, `finished`, `failed`, `framing_started`, `framing_finished` | Job changes, with `task_id` and, where available, the `duration`. `failed`: the laser reported the job as failed, e.g. after a power cut |
| Error | `limit`, `laser_control`, `laser_module`, `tilt`, `moving`, `emergency_stop`, `temperature`, `gyro`, `laser_head_fault`, `z_axis_fault`, `u_axis_fault`, `conveyor_fault`, `board_fault`, `camera_fault`, `dongle_fault`, `udisk_fault`, `machine_lock_md_fault` | Errors as the laser reports them (`tilt` / `moving`: D series; `emergency_stop` and the faults: V2 firmware) |
| Fire warning | `triggered`, `cleared` | Flame alarm, for safety automations (**X1**: `triggered` on the X1's fire alarm) |

Event entities show Unknown until their first event.

## Device Information

The device page shows the manufacturer, the model, the Model ID (the SKU's product part on V2 firmware, e.g. P0101000393, with (EU) for the SKUs under [Access control](#access-control)), the serial number and the firmware version.

## Using xTool Studio alongside Home Assistant

Running xTool Studio (or XCS) at the same time can disconnect the integration now and then, which delays updates.

## Technical Details

The protocols were worked out from xTool's own apps, mainly xTool Studio. The integration uses these ports on the laser:

- UDP 20000 and encrypted multicast (5353, 5354, 25353, 25354, 25454): discovery
- TCP 8080 / 8081: S1 and D series (HTTP, WebSocket); V1 firmware main API
- TCP 8087 / 8329: V1 firmware firmware updates and cameras
- TCP 28900: V2 firmware (TLS WebSocket)

Firmware updates are checked with xTool's cloud API (`api.xtool.com`). [docs/PROTOCOL.md](docs/PROTOCOL.md) describes the protocols in full.

## Multilanguage Support

This integration supports English and German.

## Disclaimer

This is an **independent community project**. It is not affiliated with, endorsed by, sponsored by, or otherwise associated with xTool, Makeblock, or any of their employees, contractors, or partners. All product names, trademarks, and registered trademarks (including "xTool", "xTool Studio", "XCS", and the model names referenced in this README) are the property of their respective owners and are used here for descriptive purposes only.

The integration is provided **as-is, without warranty of any kind**. It relies on reverse-engineered protocols that may change without notice in future firmware revisions. Operating a high-power laser, fiber-laser welder, or any of the supported devices through Home Assistant carries real-world safety risks (fire, blindness, burns, electrical hazards). You are responsible for following xTool's official safety guidelines, supervising every job, and ensuring proper ventilation and fire-suppression measures are in place. The author accepts **no liability** for damages, injuries, data loss, voided warranties, or any other consequences arising from the use of this integration. **Use at your own risk.**

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
