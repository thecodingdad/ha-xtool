"""Detect which features the branch under test contains.

The tests live on the ci branch and run against any branch's integration
code. Each test module is marked with the features it needs and is skipped
on branches that do not have them.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

COMPONENT = Path("custom_components/xtool")


def _imports_protocols_first() -> bool:
    text = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    protocols = text.find("from .protocols import")
    coordinator = text.find("from .coordinator import")
    return -1 < protocols < coordinator


FEATURES: dict[str, Callable[[], bool]] = {
    # The camera thumbnail refresh stops when Home Assistant stops
    "thumbnail_cancel_on_shutdown": lambda: "cancel_on_shutdown=True" in (COMPONENT / "protocols" / "ws_v2" / "entities.py").read_text(encoding="utf-8"),
    # WS-V2: a push that arrives during the accessory reads wins over the poll
    "push_after_accessory_poll": lambda: "A push that arrived while the accessories were read" in (COMPONENT / "protocols" / "ws_v2" / "coordinator.py").read_text(encoding="utf-8"),
    # P2: Studio's push channel on port 8081 alongside the REST poll
    "p2_push": lambda: (COMPONENT / "protocols" / "rest" / "p2.py").exists(),
    # P2: the poll reads what Studio reads; alarms by Studio's codes; cancelled jobs
    "p2_status": lambda: (COMPONENT / "protocols" / "rest" / "p2.py").exists() and "def describe_alarm" in (COMPONENT / "protocols" / "rest" / "p2.py").read_text(encoding="utf-8"),
    # P2: settings by Studio's keys and names; three fill lights
    "p2_settings": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists(),
    # P2: safety class as Model ID, both MACs, Work info, the other address
    "p2_device_info": lambda: (COMPONENT / "protocols" / "rest" / "p2.py").exists() and "def model_variant" in (COMPONENT / "protocols" / "rest" / "p2.py").read_text(encoding="utf-8"),
    # P2 hardware test: baseplate on when in place, safety lock on/off
    "p2_hardware_findings": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists() and "class P2Baseplate" in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    # REST lasers (P2): written values show at once and are held (coordinator.async_write)
    "rest_hold_written_values": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists() and "coordinator.async_write(" in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    # P2: no Fill light brightness number; a fill light turns on at Studio's level
    "p2_light_brightness": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists() and "use Studio's Fill light brightness" in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    # P2: a move to another address reloads the entry even during setup
    "entity_categories": lambda: "EntityCategory.DIAGNOSTIC" in (COMPONENT / "update.py").read_text(encoding="utf-8"),
    "p2_square_cells": lambda: (COMPONENT / "protocols" / "ws_v2" / "camera_correction.py").exists() and "square_cells" in (COMPONENT / "protocols" / "ws_v2" / "camera_correction.py").read_text(encoding="utf-8"),
    "p2_exposure_1000": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists() and "P2_EXPOSURE_MAX = 1000" in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    "p2_controls": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists() and "class P2SafetyLock" in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    "p2_head_controls": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists() and "def _head_lock" in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    # A photo just taken answers the picture request after a refresh
    "picture_reuse": lambda: "PICTURE_REUSE_SECONDS" in (COMPONENT / "protocols" / "ws_v2" / "entities.py").read_text(encoding="utf-8"),
    "p2_views": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists() and "def p2_correction" in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    # P2: the head's position is read on every poll
    "p2_position_every_poll": lambda: (COMPONENT / "protocols" / "rest" / "p2.py").exists() and "Where the head is, on every poll" in (COMPONENT / "protocols" / "rest" / "p2.py").read_text(encoding="utf-8"),
    "p2_move_without_reload": lambda: "def made_entry_update" in (COMPONENT / "protocols" / "rest" / "coordinator.py").read_text(encoding="utf-8"),
    # P2: the right and left fill lights follow Studio's Fill light brightness and write it
    "p2_studio_brightness": lambda: (COMPONENT / "protocols" / "rest" / "p2.py").exists() and "def _follow_studio_brightness" in (COMPONENT / "protocols" / "rest" / "p2.py").read_text(encoding="utf-8"),
    # P2: sensors that tell nothing are left out; some start turned off
    "p2_sensor_cleanup": lambda: "_P2_LEFT_OUT" in (COMPONENT / "protocols" / "rest" / "entities.py").read_text(encoding="utf-8"),
    # P2: no Reboot button (the P2 refuses /reboot)
    "p2_no_reboot": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists() and "XtoolReboot" not in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    # P2: cameras on their own devices, straightened with the P2's calibration files
    "p2_cameras": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists() and "class P2Camera(" in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    # P2: no camera photo while it sleeps (a photo wakes it)
    "p2_sleep_photo": lambda: "A photo wakes a sleeping P2" in (COMPONENT / "protocols" / "rest" / "entities.py").read_text(encoding="utf-8"),
    # P2: Ethernet first when a cable is connected, Connection sensor
    "p2_ethernet": lambda: (COMPONENT / "protocols" / "rest" / "p2.py").exists() and "def ethernet_address" in (COMPONENT / "protocols" / "rest" / "p2.py").read_text(encoding="utf-8"),
    # The first import of the package succeeds (no circular import)
    "clean_first_import": _imports_protocols_first,
    # Issue #13: switch and binary sensor icons follow the entity state
    "icon_translations": lambda: (COMPONENT / "icons.json").exists(),
    # The config poll reads Stops when moved the same way as the push
    "stops_when_moved_poll": lambda: 'self._latest["stops_when_moved"] = wm == "HANDLE"'
    not in (COMPONENT / "protocols" / "ws_v2" / "protocol.py").read_text(encoding="utf-8"),
    # The config read lists the keys it wants (F-series firmware needs that)
    "config_read_key_list": lambda: "CONFIG_READ_KEYS" in (COMPONENT / "protocols" / "ws_v2" / "protocol.py").read_text(encoding="utf-8"),
    # Issue #4: the IF2 2.0 fan is one fan entity using Studio's setFanGearV3 commands
    "if2_fan_v3": lambda: "def fan_v3_manual_mcode" in (COMPONENT / "protocols" / "accessories" / "duct_fan.py").read_text(encoding="utf-8"),
    # The IF2 2.0 fan has one Auto preset; each job sets the Auto speed
    "if2_fan_single_auto": lambda: "FANV3_PRESET_AUTO" in (COMPONENT / "protocols" / "accessories" / "duct_fan.py").read_text(encoding="utf-8"),
    # Issue #4: IF2 device page shows its firmware (and the post-run sensor has a name)
    "accessory_firmware_on_device": lambda: "def _firmware_version" in (COMPONENT / "protocols" / "accessories" / "entities.py").read_text(encoding="utf-8"),
    # Issue #4: fill-light brightness converts through Studio's 1-100 percentage
    "fill_light_studio_percent": lambda: "def _js_round" in (COMPONENT / "protocols" / "ws_v2" / "entities.py").read_text(encoding="utf-8"),
    # Issue #4: the update entity compares normalized versions; the device page follows the firmware
    "firmware_version_normalized": lambda: "parse_firmware_version" in (COMPONENT / "update.py").read_text(encoding="utf-8"),
    # Issue #4: the laser identity is read again after a reconnect and an empty machineInfo is retried
    "device_info_refetch": lambda: "self._device_info_fetched = False" in (COMPONENT / "protocols" / "ws_v2" / "coordinator.py").read_text(encoding="utf-8"),
    # Issue #4: after a restart the firmware version starts from the device page
    "firmware_from_device_page": lambda: "def _last_firmware_version" in (COMPONENT / "__init__.py").read_text(encoding="utf-8"),
    # Issue #4: F2 Ultra UV exhaust fan install state, and the fan assumed off on connect
    "exhaust_fan_install_state": lambda: "has_smoking_fan_install_state" in (COMPONENT / "protocols" / "base.py").read_text(encoding="utf-8"),
    # Issue #4: working time from machineInfo when statistics has none
    "working_time_from_machine_info": lambda: "def _latch_worktime" in (COMPONENT / "protocols" / "ws_v2" / "protocol.py").read_text(encoding="utf-8"),
    # Issue #4: F2 Ultra UV shows no MAC instead of machineInfo's wrong one
    "no_wrong_mac": lambda: "has_wrong_machine_info_mac" in (COMPONENT / "protocols" / "base.py").read_text(encoding="utf-8"),
    # Access control only on the units Studio offers it on (by SKU)
    "access_control_sku": lambda: "access_control_skus" in (COMPONENT / "protocols" / "base.py").read_text(encoding="utf-8"),
    # Auto power-off: the Power switch turns off after Idle or Sleeping for a while
    "auto_power_off": lambda: "class XtoolAutoPowerOff" in (COMPONENT / "number.py").read_text(encoding="utf-8"),
    # A value written from Home Assistant shows at once and is held until the device reports it
    "written_values_held": lambda: "def async_write" in (COMPONENT / "coordinator.py").read_text(encoding="utf-8"),
    # Task ID shows None when the laser has no job
    "task_id_none": lambda: "def task_id_value" in (COMPONENT / "const.py").read_text(encoding="utf-8"),
    # WS-V2 camera: faster photo live view, Idle when nobody watches
    "camera_photo_live_view": lambda: "MAX_SNAPSHOT_AGE" in (COMPONENT / "protocols" / "ws_v2" / "entities.py").read_text(encoding="utf-8"),
    # F2 Ultra UV main camera straightened with the laser calibration
    "camera_calibration": lambda: (COMPONENT / "protocols" / "ws_v2" / "camera_correction.py").exists(),
    # Per-camera thumbnail refresh interval number
    "camera_thumbnail_refresh": lambda: "THUMBNAIL_REFRESH_DEFAULT" in (COMPONENT / "protocols" / "ws_v2" / "entities.py").read_text(encoding="utf-8"),
    # Each camera of a multi-camera laser on its own device; the main
    # camera crop numbers and the xtool.camera action
    "camera_devices": lambda: "_camera_device_info" in (COMPONENT / "protocols" / "ws_v2" / "entities.py").read_text(encoding="utf-8"),
    # F2 Ultra UV focus height control and the xtool.focus action
    "focus_control": lambda: "has_focus_control" in (COMPONENT / "protocols" / "base.py").read_text(encoding="utf-8"),
    # F2 Ultra UV Status: Focusing, Warming up, Laser overheat
    "status_details": lambda: "has_uv_laser_status" in (COMPONENT / "protocols" / "base.py").read_text(encoding="utf-8"),
    # F2 Ultra UV Engraving lens, Wi-Fi signal and Calibration sensors
    "laser_info": lambda: "has_laser_info" in (COMPONENT / "protocols" / "base.py").read_text(encoding="utf-8"),
    # Camera photo requests share one photo per camera instead of queuing
    "camera_no_queue": lambda: "_camera_pending" in (COMPONENT / "protocols" / "ws_v2" / "protocol.py").read_text(encoding="utf-8"),
    # Focus only with the surface lens (the inner engraving lens refuses it)
    "focus_lens": lambda: "the inner engraving lens is in" in (COMPONENT / "protocols" / "ws_v2" / "protocol.py").read_text(encoding="utf-8"),
    # The red dot is assumed on after a V2 laser connects
    "red_dot_assumed_on": lambda: "_assume_red_dot_on" in (COMPONENT / "protocols" / "ws_v2" / "protocol.py").read_text(encoding="utf-8"),
    # IF2 2.0 Alert event (fan stall, cleaning needed)
    "if2_alerts": lambda: '"event": _AccessoryAlertEvent' in (COMPONENT / "protocols" / "accessories" / "entities.py").read_text(encoding="utf-8"),
    # F2 Ultra UV main camera Calibration image
    "calibration_image": lambda: (COMPONENT / "image.py").exists(),
    # A WS-V2 job the laser reports as failed is a Job "failed" event
    "job_failed": lambda: '"failed" if failed else "finished"' in (COMPONENT / "protocols" / "ws_v2" / "protocol.py").read_text(encoding="utf-8"),
    # F2 Ultra UV Last job time from the job result push
    "job_result_time": lambda: "has_job_result_time" in (COMPONENT / "protocols" / "base.py").read_text(encoding="utf-8"),
    # Device registry lookups without the helpers Home Assistant 2026.11 deprecates
    "device_registry_lookup": lambda: "async_entries_for_config_entry" in (COMPONENT / "coordinator.py").read_text(encoding="utf-8"),
    # The WS-V2 poll does not read the settings of a sleeping laser (the read wakes it)
    # P2: one Overlay close camera, Mode overlays, Fill light (close) on the
    # Close camera, exposure read back, Wake button
    "p2_overlays_v2": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists()
    and 'CLOSE_CAMERA_IMAGE = "close_camera"' in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    # Crop center numbers use the align-center icons
    "crop_center_icons": lambda: "mdi:format-horizontal-align-center" in (COMPONENT / "protocols" / "ws_v2" / "entities.py").read_text(encoding="utf-8"),
    # Status Initializing while the laser starts after the Power switch turns on
    "power_on_starting": lambda: "POWER_ON_STARTUP_SECONDS" in (COMPONENT / "coordinator.py").read_text(encoding="utf-8"),
    # Start-up ends once the laser reports its first status after power on
    "startup_ends_at_first_status": lambda: "def async_update_listeners" in (COMPONENT / "coordinator.py").read_text(encoding="utf-8"),
    # Readings unavailable while the laser is off; lifetime totals keep their last value
    "sensors_unavailable_when_off": lambda: "LIFETIME_TOTAL_KEYS" in (COMPONENT / "entity.py").read_text(encoding="utf-8"),
    # The lens is fitted to the measured part of a calibration grid and replaces its continued points
    "measured_bounds": lambda: (COMPONENT / "protocols" / "ws_v2" / "camera_correction.py").exists()
    and "def _measured_bounds" in (COMPONENT / "protocols" / "ws_v2" / "camera_correction.py").read_text(encoding="utf-8"),
    # P2 head travel 610 x 305 (where the P2 stops), not Studio's 620 x 308
    "p2_real_travel": lambda: (COMPONENT / "protocols" / "rest" / "p2.py").exists()
    and "P2_HEAD_MAX_X = 610.0" in (COMPONENT / "protocols" / "rest" / "p2.py").read_text(encoding="utf-8"),
    # P2 Measure reads the sensor's own distance, after the sensor has started
    "p2_sensor_distance": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists()
    and "P2_SENSOR_START_SECONDS" in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    # P2 exposure: read on the first poll after waking, last value restored; no Last distance sensor
    "p2_exposure_on_wake": lambda: (COMPONENT / "protocols" / "rest" / "p2.py").exists()
    and "self._was_asleep" in (COMPONENT / "protocols" / "rest" / "p2.py").read_text(encoding="utf-8"),
    # P2 Measure button and Measured sensor on the Overview camera's device
    "p2_measure_on_overview": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists()
    and "class P2Measured(_CameraDeviceEntity" in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    # P2 photos are shared and taken one at a time; live view at most 1 frame/s
    "p2_camera_queue": lambda: (COMPONENT / "protocols" / "rest" / "p2.py").exists()
    and "def _camera_snap_locked" in (COMPONENT / "protocols" / "rest" / "p2.py").read_text(encoding="utf-8"),
    # Picture requests reuse a photo younger than the thumbnail refresh interval
    "picture_follows_refresh": lambda: "timedelta(seconds=self._thumbnail_seconds)" in (COMPONENT / "protocols" / "ws_v2" / "entities.py").read_text(encoding="utf-8"),
    # P2: no new photo for a while after a slow one
    "p2_photo_rest": lambda: (COMPONENT / "protocols" / "rest" / "p2.py").exists()
    and "PHOTO_REST_MAX" in (COMPONENT / "protocols" / "rest" / "p2.py").read_text(encoding="utf-8"),
    # A picture request answers with the last photo and takes the new one meanwhile
    "picture_at_once": lambda: "_background_photo" in (COMPONENT / "protocols" / "ws_v2" / "entities.py").read_text(encoding="utf-8"),
    # P2: on Wi-Fi with a cable connected, Ethernet is tried again every few minutes
    "p2_back_to_ethernet": lambda: "ETHERNET_CHECK_SECONDS" in (COMPONENT / "protocols" / "rest" / "coordinator.py").read_text(encoding="utf-8"),
    # A P2 camera reads the head position only for a new photo
    "p2_head_read_per_photo": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists()
    and "A request answered with the last photo needs nothing new" in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    # The xtool.camera action switches a P2 camera's Crop preset to Custom
    "p2_camera_action_custom_crop": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists()
    and "def _use_custom_crop" in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    # The P2's exposure was read back (it always answers auto, 0), until that was dropped
    "p2_exposure_read": lambda: (COMPONENT / "protocols" / "rest" / "p2.py").exists()
    and "def read_camera_exposure" in (COMPONENT / "protocols" / "rest" / "p2.py").read_text(encoding="utf-8"),
    # P2 exposure: Studio's value after the Power switch turns on, unknown after it turns off
    "p2_exposure_studio": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists()
    and "P2_STUDIO_EXPOSURE" in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    # The P2 has no Last distance sensor
    "p2_no_last_distance": lambda: "The Measured sensor has it as the attribute" in (COMPONENT / "protocols" / "rest" / "entities.py").read_text(encoding="utf-8"),
    # An accessory whose info M-code does not answer is not keyed by its MAC
    "accessory_serial_key": lambda: "self._accessory_serials" in (COMPONENT / "coordinator.py").read_text(encoding="utf-8"),
    # DHCP: a configured laser is found by its MAC and follows its new address
    "dhcp_follows_mac": lambda: "def _async_follow_address" in (COMPONENT / "config_flow.py").read_text(encoding="utf-8"),
    # A laser whose Power switch is off is not polled
    "skip_polling_while_off": lambda: "async def _async_poll" in (COMPONENT / "coordinator.py").read_text(encoding="utf-8"),
    # A failed poll's error is logged with its type
    "poll_error_type": lambda: '"WS-V2 poll failed: %r"' in (COMPONENT / "protocols" / "ws_v2" / "coordinator.py").read_text(encoding="utf-8"),
    # xtool.camera checks crop values against each camera's own limits
    "camera_limits_per_camera": lambda: (COMPONENT / "services.py").exists() and "camera_value_out_of_range" in (COMPONENT / "services.py").read_text(encoding="utf-8"),
    # Status is Not responding while the Power switch is on and the laser does not answer
    "status_not_responding": lambda: 'NOT_RESPONDING = "not_responding"' in (COMPONENT / "const.py").read_text(encoding="utf-8"),
    # WS-V2: Connection sensor from the laser's own interface list
    "wsv2_connection": lambda: "def connection_of_interface" in (COMPONENT / "protocols" / "ws_v2" / "protocol.py").read_text(encoding="utf-8"),
    # P2: each overlay switch has position and style attributes; Overlays select (Integration or Card)
    "p2_overlays": lambda: (COMPONENT / "protocols" / "rest" / "p2_entities.py").exists()
    and "def _set_overlay_attributes" in (COMPONENT / "protocols" / "rest" / "p2_entities.py").read_text(encoding="utf-8"),
    "sleep_no_slow_reads": lambda: "slow = not asleep" in (COMPONENT / "protocols" / "ws_v2" / "protocol.py").read_text(encoding="utf-8"),
    # WS-V2: the Connection is read first in each poll until it answers, once per connection
    "connection_first": lambda: "async def _poll_connection" in (COMPONENT / "protocols" / "ws_v2" / "protocol.py").read_text(encoding="utf-8"),
    # WS-V2: the Connection and laser information reach Home Assistant as soon as they are read
    # A laser that stops answering is looked for with xTool discovery; moves go through the config flow
    "address_by_discovery": lambda: "def async_search_address" in (COMPONENT / "coordinator.py").read_text(encoding="utf-8"),
    # V1 discovery sends a requestId the P2 can echo (it fits in 32 bits)
    "v1_request_id_fits": lambda: "random.randint(1, 0x7FFFFFFF)" in (COMPONENT / "discovery.py").read_text(encoding="utf-8"),
    "laser_info_notify_now": lambda: "def _notify_now" in (COMPONENT / "protocols" / "ws_v2" / "protocol.py").read_text(encoding="utf-8"),
}


def has(name: str) -> bool:
    """Return True if the branch under test has the feature."""
    return FEATURES[name]()


def requires(*names: str) -> pytest.MarkDecorator:
    """Skip unless the branch under test has every named feature."""
    missing = [name for name in names if not has(name)]
    return pytest.mark.skipif(
        bool(missing), reason=f"branch lacks {', '.join(missing)}"
    )
