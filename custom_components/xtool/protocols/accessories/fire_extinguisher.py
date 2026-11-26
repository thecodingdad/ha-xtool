"""FireExtinguisher + V1.5 + Fire Safety Set 2 (SafetyFireBoxPro).

The Fire Safety Set and v1.5 ship as a **stub** (sn + fw version only)
until a user with the accessory provides a debug-log capture. The Fire
Safety Set 2 is read as Studio reads it (``getSafetyFireBoxProInfo``),
checked against one on an X1.
"""

from __future__ import annotations

from .base import (
    MCODE_FIRE_BOX_INFO,
    AccessoryDefinition,
    AccessoryEntitySpec,
    num,
    quoted,
    version_only,
)


_ENTITIES = (
    AccessoryEntitySpec("sensor", "version", field="version",
                        icon="mdi:numeric", entity_category="diagnostic"),
    AccessoryEntitySpec("sensor", "sn", field="sn",
                        icon="mdi:identifier", entity_category="diagnostic"),
)


# Placeholder prefix bytes — same as DuctFan until logs reveal the
# real BT discriminator.
_PREFIX = bytes([70, 115, 99, 1, 0])


FIRE_EXTINGUISHER = AccessoryDefinition(
    type_id="FireExtinguisher",
    friendly_name="xTool Fire Safety Set",
    prefix=_PREFIX,
    firmware_content_id="xTool-extinguisherBox-firmware",
    info_mcode=None,
    parse_info=version_only,
    entities=_ENTITIES,
)


FIRE_EXTINGUISHER_V1_5 = AccessoryDefinition(
    type_id="FireExtinguisherV1_5",
    friendly_name="xTool Fire Safety Set v1.5",
    prefix=_PREFIX,
    firmware_content_id="xTool-extinguisherBox-firmware",
    info_mcode=None,
    parse_info=version_only,
    entities=_ENTITIES,
)


# Error bits of the ``S`` value, as Studio's error dialogs name them:
# 4 no gas cylinder (also reported as ``G-1``), 8 gas insufficient,
# 16 fire extinguisher drive abnormal, 32 the cylinder needs calibrating.
_FIRE_BOX_NO_CYLINDER = 4
_FIRE_BOX_GAS_LOW = 8
_FIRE_BOX_DRIVE_FAULT = 16
_FIRE_BOX_CALIBRATION = 32


def parse_fire_box_info(text: str) -> dict[str, object]:
    """Decode the Fire Safety Set 2's ``M9033`` reply (head stripped).

    Wire shape: ``A<version> J<spraying> S<error bits> G<cylinder>
    T<gas remaining> W<…> E:"<sn>"``, e.g.
    ``A40.223.004.5222.01.B02 J0 S0 G3 T410 W1190 E:"…"``. ``G`` is -1
    without a cylinder and 0-3 (the gas level Studio shows) with one;
    with bit 4 of ``S`` set there is no cylinder, whatever ``G`` says.
    ``J`` is 1 while it sprays; ``W`` is the weight (Studio's ``weight``).
    """
    fields: dict[str, object] = {
        "version": text.split(" ", 1)[0].removeprefix("A") or None,
        "sn": quoted(text, "E:"),
    }
    errors = num(text, "S")
    cylinder = num(text, "G")
    if errors is not None and int(errors) & _FIRE_BOX_NO_CYLINDER:
        fields["cylinder_installed"] = False
        fields["gas_level"] = 0
    elif cylinder is not None:
        installed = int(cylinder) in (0, 1, 2, 3)
        fields["cylinder_installed"] = installed
        fields["gas_level"] = int(cylinder) if installed else 0
    gas = num(text, "T")
    if gas is not None:
        fields["gas_remaining"] = int(gas)
    spraying = num(text, "J")
    if spraying is not None:
        fields["spraying"] = int(spraying) != 0
    if errors is not None:
        bits = int(errors)
        fields["gas_low"] = bool(bits & _FIRE_BOX_GAS_LOW)
        fields["drive_fault"] = bool(bits & _FIRE_BOX_DRIVE_FAULT)
        fields["calibration_needed"] = bool(bits & _FIRE_BOX_CALIBRATION)
    return fields


_FIRE_BOX_ENTITIES = (
    *_ENTITIES,
    AccessoryEntitySpec("binary_sensor", "spraying", field="spraying"),
    AccessoryEntitySpec("binary_sensor", "cylinder_installed",
                        field="cylinder_installed", device_class="plug"),
    AccessoryEntitySpec("sensor", "gas_level", field="gas_level",
                        icon="mdi:gauge", state_class="measurement"),
    AccessoryEntitySpec("binary_sensor", "gas_low", field="gas_low",
                        device_class="problem"),
    AccessoryEntitySpec("binary_sensor", "drive_fault", field="drive_fault",
                        device_class="problem"),
    AccessoryEntitySpec("binary_sensor", "calibration_needed",
                        field="calibration_needed", device_class="problem"),
    AccessoryEntitySpec("sensor", "gas_remaining", field="gas_remaining",
                        icon="mdi:gas-cylinder", entity_category="diagnostic"),
)


SAFETY_FIRE_BOX_PRO = AccessoryDefinition(
    type_id="SafetyFireBoxPro",
    # Studio's name for it (``accessory.safetyFireBoxPro.name``)
    friendly_name="xTool Fire Safety Set 2",
    # Studio's F0F7 prefix for it (type 82, sub-type 111)
    prefix=bytes([82, 115, 111, 1, 0]),
    firmware_content_id="xTool-SafetyFireBoxPro-firmware",
    info_mcode=MCODE_FIRE_BOX_INFO,
    parse_info=parse_fire_box_info,
    entities=_FIRE_BOX_ENTITIES,
    platform_id=82,
)
