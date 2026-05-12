"""SafetyPro AP2 — desktop 5-filter HEPA-stack air-cleaner.

Same physical device class across the xTool product line:

- S1 AP2 (M-code WS, ``M9039`` reply: ``E<n> F<n> G<n> H<n> I<n>``)
- WS-V2 / REST / D-series cabinet (``M9033`` reply:
  ``H<n> I<n> J<n> K<n> L<n>``)

Studio's V2 bundle ships anonymous token names (``H``..``L``)
because the same 5-filter parser is reused across variants. The
AP2 datasheet names them ``pre`` / ``medium`` / ``carbon`` /
``dense_carbon`` / ``hepa`` — that's how the entity layer
exposes the fields here.

The optional ``running`` binary + ``purifier_sensor_d`` /
``purifier_sensor_s`` particulate sensors are S1-only on current
firmware; the entity builder's field-skip guard means cabinet
purifiers don't gain phantom sensors.

The laser-host purifier toggles (``purifier_check``,
``purifier_continue``, ``purifier_timeout``) live here because
they are AP2-specific even though the wire path is the laser's
own ``/v1/device/configs`` API — they only make sense when an
AP2 is actually paired. Coordinator-side augmentation merges
the laser-state values into the accessory ``fields`` dict so
the entity layer's field-presence guard suppresses them on
families where the AP2 isn't connected.

Prefix bytes: ``[69,115,96,1,0]`` ("Es`") — ``yd`` in bundle.

Larger cabinet / floor-standing variants (xTool SafetyPro AP2
Large, AP2 Max) live in :mod:`large_purifier`.
"""

from __future__ import annotations

from typing import Any

from .base import (
    MCODE_PURIFIER_INFO,
    MCODE_PURIFIER_RESET_FILTER,
    MCODE_PURIFIER_SET_GEAR,
    AccessoryDefinition,
    AccessoryEntitySpec,
    num,
    quoted,
)


async def _set_config(coordinator: Any, key: str, value: Any) -> None:
    """Forward a laser-host config-write through whatever helper the
    family exposes. WS-V2 has ``set_config``; REST V1 has it as
    well; S1 doesn't (these endpoints are V2-firmware-only).
    """
    set_config = getattr(coordinator.protocol, "set_config", None)
    if set_config is None:
        return
    await set_config(key, value)


async def _set_peripheral(
    coordinator: Any, peripheral_type: str, **payload: Any,
) -> None:
    """Forward a peripheral-API call. Only V2 firmware exposes
    ``set_peripheral`` — S1 / REST / D-series do not.
    """
    set_peripheral = getattr(coordinator.protocol, "set_peripheral", None)
    if set_peripheral is None:
        return
    await set_peripheral(peripheral_type, **payload)


_PURIFIER_SPEED_LABEL_TO_INT = {"off": 0, "low": 1, "medium": 2, "high": 3}
_PURIFIER_SPEED_INT_TO_LABEL = {
    v: k for k, v in _PURIFIER_SPEED_LABEL_TO_INT.items()
}


async def _purifier_speed_select_write(coordinator: Any, option: Any) -> None:
    """Set external-purifier speed via the laser's peripheral API.

    Distinct from the BT-tunneled ``M9039`` gear setter — V2
    firmware exposes a dedicated ``ext_purifier`` peripheral that
    handles the legacy XCS-style speed presets (off / low /
    medium / high). The BT path lives alongside as
    ``gear_select`` for direct M-code control.
    """
    raw = _PURIFIER_SPEED_LABEL_TO_INT.get(str(option))
    if raw is None:
        return
    await _set_peripheral(
        coordinator, "ext_purifier", action="set_speed", value=raw,
    )


def parse_purifier_info(text: str) -> dict[str, object]:
    """Decode ``M9033`` reply (V2 cabinet purifier).

    Wire shape: ``<v1> <v2> <gear> H<H> I<I> J<J> K<K> L<L> E:"<sn>"``.
    H/I/J/K/L map onto the AP2 datasheet's named filters in
    order: pre / medium / carbon / dense_carbon / hepa.
    """
    parts = text.split(" ", 3)
    return {
        "version": " ".join(parts[:2]) if len(parts) >= 2 else None,
        "gear": parts[2] if len(parts) >= 3 else None,
        "filter_pre": num(text, "H"),
        "filter_medium": num(text, "I"),
        "filter_carbon": num(text, "J"),
        "filter_dense_carbon": num(text, "K"),
        "filter_hepa": num(text, "L"),
        "sn": quoted(text, "E:"),
    }


PURIFIER_ENTITIES = (
    AccessoryEntitySpec("sensor", "version", field="version",
                        icon="mdi:numeric", entity_category="diagnostic"),
    AccessoryEntitySpec("sensor", "sn", field="sn",
                        icon="mdi:identifier", entity_category="diagnostic"),
    AccessoryEntitySpec("sensor", "gear", field="gear",
                        icon="mdi:fan"),
    AccessoryEntitySpec("binary_sensor", "running", field="running",
                        icon="mdi:fan",
                        device_class="running"),
    AccessoryEntitySpec("sensor", "filter_pre", field="filter_pre",
                        icon="mdi:air-filter", unit="%"),
    AccessoryEntitySpec("sensor", "filter_medium", field="filter_medium",
                        icon="mdi:air-filter", unit="%"),
    AccessoryEntitySpec("sensor", "filter_carbon", field="filter_carbon",
                        icon="mdi:air-filter", unit="%"),
    AccessoryEntitySpec("sensor", "filter_dense_carbon",
                        field="filter_dense_carbon",
                        icon="mdi:air-filter", unit="%"),
    AccessoryEntitySpec("sensor", "filter_hepa", field="filter_hepa",
                        icon="mdi:air-filter", unit="%"),
    AccessoryEntitySpec("sensor", "purifier_sensor_d",
                        field="purifier_sensor_d",
                        icon="mdi:gauge",
                        entity_category="diagnostic"),
    AccessoryEntitySpec("sensor", "purifier_sensor_s",
                        field="purifier_sensor_s",
                        icon="mdi:gauge",
                        entity_category="diagnostic"),
    AccessoryEntitySpec("select", "gear_select", field="gear",
                        icon="mdi:fan", options=("0", "1", "2", "3"),
                        write_mcode=lambda gear: f"{MCODE_PURIFIER_SET_GEAR} {gear}"),
    AccessoryEntitySpec("button", "reset_filter",
                        icon="mdi:filter-remove",
                        write_mcode=f"{MCODE_PURIFIER_RESET_FILTER} 0"),
    # --- Laser-host AP2 controls (V2-firmware families only) ---
    # The coordinator merges laser-state values into the accessory
    # ``fields`` dict so families that don't expose these endpoints
    # (S1 raw WS) skip the entity via the field-presence guard.
    AccessoryEntitySpec(
        "select", "speed_select", field="purifier_speed",
        icon="mdi:air-purifier",
        options=("off", "low", "medium", "high"),
        write_action=lambda coord, opt: _purifier_speed_select_write(coord, opt),
        entity_category="config",
    ),
    AccessoryEntitySpec(
        "switch", "check_enabled", field="purifier_check",
        icon="mdi:air-purifier",
        write_action=lambda coord, val: _set_config(
            coord, "purifierCheck", bool(val),
        ),
        entity_category="config",
    ),
    AccessoryEntitySpec(
        "switch", "auto_continue", field="purifier_continue",
        icon="mdi:autorenew",
        write_action=lambda coord, val: _set_config(
            coord, "purifierContinue", bool(val),
        ),
        entity_category="config",
    ),
    AccessoryEntitySpec(
        "number", "auto_off_timeout", field="purifier_timeout",
        icon="mdi:timer-cog", unit="s",
        min_value=0, max_value=3600, step=30,
        write_action=lambda coord, val: _set_config(
            coord, "purifierTimeout", int(val),
        ),
        entity_category="config",
    ),
)


PURIFIER = AccessoryDefinition(
    type_id="Purifier",
    friendly_name="xTool SafetyPro AP2",
    prefix=bytes([69, 115, 96, 1, 0]),
    firmware_content_id="xTool-bigPurifier-firmware",
    info_mcode=MCODE_PURIFIER_INFO,
    parse_info=parse_purifier_info,
    entities=PURIFIER_ENTITIES,
)
