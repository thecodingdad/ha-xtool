"""AirPump / AirPumpV2 — laser air-assist pumps.

BT-paired air-pressure pump. Reuses the DuctFan ``M9082`` parser
(gear / buzzer / sn / version share the same wire shape).

On the S1, the air-assist state actually rides the laser's own
``M15`` push (level + enabled) and ``M1099`` (close-delay)
M-codes — not via ``M9082``. S1Coordinator merges those fields
into the AirPump accessory state so the same entity surface
covers both the BT-tunneled (V2 / REST / D-series) and the
M-code-WS-driven (S1) variants.

Prefix bytes: ``[70,115,99,1,0]`` (shares with DuctFan; the
``type_id`` distinguishes them in the resolver).
"""

from __future__ import annotations

from typing import Any

from .base import (
    MCODE_AIR_ASSIST_DELAY,
    MCODE_FAN_INFO,
    AccessoryDefinition,
    AccessoryEntitySpec,
)
from .duct_fan import parse_fan_info


async def _close_delay_write(coordinator: Any, value: Any) -> None:
    """Family-aware close-delay write.

    On S1 the ``M1099 T<n>`` M-code goes through the laser-host
    WS (via ``send_command`` — ``M1099`` is in
    ``LASER_HOST_MCODES``). On WS-V2 / REST V1 / D-series there's
    a dedicated ``airAssistDelay`` config key behind
    ``protocol.set_config``; we prefer that when available so the
    write hits the same endpoint Studio uses for the V2 family.
    """
    proto = coordinator.protocol
    set_config = getattr(proto, "set_config", None)
    if set_config is not None:
        await set_config("airAssistDelay", int(value))
        return
    send_command = getattr(proto, "send_command", None)
    if send_command is not None:
        await send_command(f"{MCODE_AIR_ASSIST_DELAY} T{int(value)}")


async def _gear_default_write(
    coordinator: Any, value: Any, *, config_key: str,
) -> None:
    """Set the per-job-mode default air-assist gear (V2 firmware only).

    ``airassistCut`` / ``airassistGrave`` config keys are exposed
    by every WS-V2 Studio bundle; S1 has no equivalent (gear
    defaults live on the laser-MCU directly).
    """
    proto = coordinator.protocol
    set_config = getattr(proto, "set_config", None)
    if set_config is None:
        return
    await set_config(config_key, int(value))


_ENTITIES = (
    AccessoryEntitySpec("sensor", "version", field="version",
                        icon="mdi:numeric", entity_category="diagnostic"),
    AccessoryEntitySpec("sensor", "sn", field="sn",
                        icon="mdi:identifier", entity_category="diagnostic"),
    # ``gear`` is firmware-driven (the laser switches air-assist
    # speed automatically based on the running job's cut /
    # engrave segments). Exposing it as a read-only sensor —
    # toggling the level from HA between job steps would fight
    # the laser's own G-code stream.
    AccessoryEntitySpec("sensor", "gear", field="gear",
                        icon="mdi:pump"),
    AccessoryEntitySpec("binary_sensor", "running", field="running",
                        icon="mdi:fan",
                        device_class="running"),
    # ``connected`` = M15 ``A=1`` flag — the air-assist hardware
    # is plugged into the laser. Distinct from ``running`` which
    # requires both A=1 **and** gear > 0.
    AccessoryEntitySpec("binary_sensor", "connected", field="connected",
                        icon="mdi:power-plug",
                        device_class="connectivity",
                        entity_category="diagnostic"),
    AccessoryEntitySpec("number", "close_delay", field="close_delay",
                        icon="mdi:timer-cog", unit="s",
                        min_value=0, max_value=600, step=1,
                        write_action=_close_delay_write,
                        # Keep the M-code form as a documentation
                        # hint for the routing helper; the V2
                        # set_config path wins via write_action.
                        write_mcode=lambda val: f"{MCODE_AIR_ASSIST_DELAY} T{int(val)}",
                        entity_category="config"),
    # Per-mode default-gear settings — only present on families
    # that populate ``state.air_assist_gear_cut`` etc. into the
    # accessory fields (V2 firmware). S1 doesn't expose this
    # surface, so the field-skip guard hides them there.
    AccessoryEntitySpec(
        "number", "gear_default_cut", field="air_assist_gear_cut",
        icon="mdi:fan", min_value=0, max_value=4, step=1,
        write_action=lambda coord, val: _gear_default_write(
            coord, val, config_key="airassistCut",
        ),
        entity_category="config",
    ),
    AccessoryEntitySpec(
        "number", "gear_default_engrave", field="air_assist_gear_grave",
        icon="mdi:fan", min_value=0, max_value=4, step=1,
        write_action=lambda coord, val: _gear_default_write(
            coord, val, config_key="airassistGrave",
        ),
        entity_category="config",
    ),
)


AIR_PUMP = AccessoryDefinition(
    type_id="AirPump",
    friendly_name="xTool Smart Air Assist",
    prefix=bytes([70, 115, 99, 1, 0]),
    firmware_content_id="xTool-airpump1.0-firmware",
    info_mcode=MCODE_FAN_INFO,
    parse_info=parse_fan_info,
    entities=_ENTITIES,
)


AIR_PUMP_V2 = AccessoryDefinition(
    type_id="AirPumpV2",
    friendly_name="xTool Air-Compress Assist",
    prefix=bytes([70, 115, 99, 1, 0]),
    firmware_content_id="xTool-airpump2.0-firmware",
    info_mcode=MCODE_FAN_INFO,
    parse_info=parse_fan_info,
    entities=_ENTITIES,
)
