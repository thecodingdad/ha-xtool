"""Model registry entries for the WS-V2 family.

Each V1 model that has a V2 firmware sibling gets its own V2 entry here.
``model_id`` matches the V1 sibling so device-name discovery stays
consistent; ``protocol_version="V2"`` disambiguates the two registry
rows. Capability flags mirror the V1 sibling for caps that map 1:1
to the V2 entity set; V1-only caps (`has_ir_led`, `has_distance_measure`,
camera-exposure, etc.) get reset because the V2 endpoint mapping has
not yet been verified.

Future V2-only models (devices that ship without a V1 firmware line)
add a single entry here with ``protocol_version="V2"`` — no V1 stub
sibling needed; the candidate-list step in ``validate_connection``
returns the V2 entry directly.
"""

from __future__ import annotations

from ..base import XtoolDeviceModel

from .coordinator import WSV2Coordinator
from .protocol import WSV2Protocol


XTOOL_F1_WSV2 = XtoolDeviceModel(
    model_id="F1",
    name="xTool F1",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_machine_lock=True,
    has_mode_switch=True,
    firmware_content_id="xTool-f1-firmware",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("F1",),
)


XTOOL_F1_ULTRA_WSV2 = XtoolDeviceModel(
    model_id="F1Ultra",
    name="xTool F1 Ultra",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_machine_lock=True,
    has_mode_switch=True,
    has_water_cooling=True,
    has_uv_fire=True,
    has_gyro=True,
    has_display_screen=True,
    firmware_content_id="xTool-f1-ultra-firmware-1.5",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("F1Ultra",),
)


# F1 Ultra V2 (GS003) — Class-1 safe variant. The "V2" in the name is
# xTool's product label, not the protocol version. This entry covers
# the V2-firmware sibling of GS003.
XTOOL_F1_ULTRA_V2_WSV2 = XtoolDeviceModel(
    model_id="GS003",
    name="xTool F1 Ultra V2",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_machine_lock=True,
    has_mode_switch=True,
    has_water_cooling=True,
    has_uv_fire=True,
    has_gyro=True,
    has_display_screen=True,
    firmware_content_id="xTool-f1-ultra-class1-firmware-1.5",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("GS003",),
)


XTOOL_F1_LITE_WSV2 = XtoolDeviceModel(
    model_id="GS005",
    name="xTool F1 Lite",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_machine_lock=True,
    firmware_content_id="xTool-f1-lite-firmware",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("GS005",),
)


XTOOL_F2_WSV2 = XtoolDeviceModel(
    model_id="F2",
    name="xTool F2",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_machine_lock=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    firmware_content_id="xTool-f2-firmware",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("F2",),
)


XTOOL_F2_ULTRA_WSV2 = XtoolDeviceModel(
    model_id="F2Ultra",
    name="xTool F2 Ultra",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_machine_lock=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    firmware_content_id="xTool-f2-ultra-firmware",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("F2Ultra",),
)


XTOOL_F2_ULTRA_SINGLE_WSV2 = XtoolDeviceModel(
    model_id="F2UltraSingle",
    name="xTool F2 Ultra (Single)",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_machine_lock=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    firmware_content_id="xTool-f2-ultra-single-firmware",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("F2UltraSingle",),
)


XTOOL_F2_ULTRA_UV_WSV2 = XtoolDeviceModel(
    model_id="F2UltraUV",
    name="xTool F2 Ultra UV",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_machine_lock=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    has_uv_fire=True,
    firmware_content_id="xTool-f2-ultra-uv-firmware",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("F2UltraUV",),
)


XTOOL_M1_ULTRA_WSV2 = XtoolDeviceModel(
    model_id="M1Ultra",
    name="xTool M1 Ultra",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_z_axis=True,
    has_drawer=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    has_gyro=True,
    firmware_content_id="xTool-m1-ultra-firmware",
    firmware_machine_type="MLM",
    protocol_version="V2",
    discovery_match=("M1Ultra",),
)


XTOOL_P2S_WSV2 = XtoolDeviceModel(
    model_id="P2S",
    name="xTool P2S",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_z_axis=True,
    has_drawer=True,
    has_cover_sensor=True,
    has_lid_sensor=True,
    has_mode_switch=True,
    has_uv_fire=True,
    has_gyro=True,
    firmware_content_id="xTool-p2s-firmware",
    firmware_machine_type="MXP",
    protocol_version="V2",
    discovery_match=("P2S",),
)


XTOOL_P3_WSV2 = XtoolDeviceModel(
    model_id="P3",
    name="xTool P3",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_z_axis=True,
    has_drawer=True,
    has_cover_sensor=True,
    has_lid_sensor=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    has_water_cooling=True,
    firmware_content_id="xTool-p3-firmware",
    firmware_machine_type="MXP",
    protocol_version="V2",
    discovery_match=("P3",),
)


XTOOL_METALFAB_WSV2 = XtoolDeviceModel(
    model_id="METALFAB",
    name="xTool MetalFab",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_z_axis=True,
    has_drawer=True,
    has_cover_sensor=True,
    has_lid_sensor=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    # MetalFab firmware uses gyro internally for tilt-error detection
    # but does not expose a periodic peripheral query for it (verified
    # against the HJ003 Studio bundle). Leave the cap flag off so the
    # gyro sensors don't surface as permanently-unavailable.
    firmware_content_id="xTool-hj003-firmware",
    firmware_machine_type="MHJ",
    protocol_version="V2",
    discovery_match=("METALFAB", "HJ003"),
)


XTOOL_APPAREL_PRINTER_WSV2 = XtoolDeviceModel(
    model_id="DT001",
    name="xTool Apparel Printer",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    firmware_content_id="xTool-apparelprinter-firmware-1.5",
    firmware_machine_type="MDT",
    protocol_version="V2",
    discovery_match=("DT001",),
)


WSV2_MODELS: tuple[XtoolDeviceModel, ...] = (
    XTOOL_F1_WSV2,
    XTOOL_F1_ULTRA_WSV2,
    XTOOL_F1_ULTRA_V2_WSV2,
    XTOOL_F1_LITE_WSV2,
    XTOOL_F2_WSV2,
    XTOOL_F2_ULTRA_WSV2,
    XTOOL_F2_ULTRA_SINGLE_WSV2,
    XTOOL_F2_ULTRA_UV_WSV2,
    XTOOL_M1_ULTRA_WSV2,
    XTOOL_P2S_WSV2,
    XTOOL_P3_WSV2,
    XTOOL_METALFAB_WSV2,
    XTOOL_APPAREL_PRINTER_WSV2,
)
