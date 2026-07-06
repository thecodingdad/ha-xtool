"""DataUpdateCoordinator base class for xTool laser devices.

Each protocol family ships its own coordinator subclass in
``protocols/<family>/coordinator.py``. The subclass owns the polling
loop (``_async_update_data``), device-info fetch (``_fetch_device_info``),
all family-specific state (e.g. AP2, XCS, laser/wifi firmware versions,
workspace dims), and the ``build_<platform>()`` factories.

The base only carries cross-cutting state every family populates plus a
small set of generic helpers.
"""

from __future__ import annotations

import logging
from datetime import timedelta
import asyncio
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DEFAULT_DONGLE_POLL_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STATS_POLL_INTERVAL,
    DOMAIN,
    FIRMWARE_CHECK_INTERVAL,
    XtoolStatus,
)

# Import directly from protocols.base — pulling from the protocols package
# would eagerly load every family's __init__.py, which in turn loads each
# family's models.py → coordinator.py, creating an import cycle.
from .protocols.base import (
    AccessoryState,
    LaserInfo,
    XtoolDeviceModel,
    XtoolDeviceState,
    XtoolProtocol,
)

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor import BinarySensorEntity
    from homeassistant.components.button import ButtonEntity
    from homeassistant.components.camera import Camera
    from homeassistant.components.event import EventEntity
    from homeassistant.components.fan import FanEntity
    from homeassistant.components.light import LightEntity
    from homeassistant.components.number import NumberEntity
    from homeassistant.components.select import SelectEntity
    from homeassistant.components.sensor import SensorEntity
    from homeassistant.components.switch import SwitchEntity
    from homeassistant.components.update import UpdateEntity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)


# M9098 → accessory type-id resolver. The numeric token in
# Studio's `getAllDangleConnectList` response indexes into the
# ``Te.*`` enum. Used by every family that exposes the F0F7
# ``/passthrough`` (REST V1, D-series) or ``/v1/parts/control``
# (WS-V2) tunnels. S1 doesn't go through this resolver — its
# accessory surface is fed directly from ``M1098`` + the
# ``M9039`` push cache (see ``S1Coordinator._poll_accessories``).
_TE_TYPE_ID_BY_NUMERIC: dict[int, str] = {
    0x32: "FireExtinguisherV1_5",
    0x34: "Purifier",
    0x3D: "AirPump",
    0x40: "AirPumpV2",
    0x46: "DuctFan",
    0x4A: "Dongle",
    0x4B: "UvSensor",
    0x4C: "LargePurifierV3",
    0x4E: "DuctFanV3",
    0x52: "SafetyFireBoxPro",
    0x53: "MultiFunctionalBase",
    0x54: "BackpackPurifier",
}


def _resolve_type_id(row: dict[str, Any]) -> str | None:
    """Map an M9098 row to an :class:`AccessoryDefinition` type id.

    Unknown ids return ``None`` and are filtered out by the
    accessory walk; the registry extends as new types land in
    issue-tracker logs.
    """
    raw = row.get("type_id_raw")
    if not isinstance(raw, (int, float)):
        return None
    return _TE_TYPE_ID_BY_NUMERIC.get(int(raw))


class XtoolCoordinator(DataUpdateCoordinator[XtoolDeviceState]):
    """Base coordinator. One subclass per protocol family."""

    def __init__(
        self,
        hass: HomeAssistant,
        protocol: XtoolProtocol,
        device_name: str,
        serial_number: str,
        firmware_version: str,
        model: XtoolDeviceModel,
        power_switch_entity_id: str | None = None,
        enable_firmware_updates: bool = False,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        firmware_check_interval: int = FIRMWARE_CHECK_INTERVAL,
        stats_poll_interval: int = DEFAULT_STATS_POLL_INTERVAL,
        dongle_poll_interval: int = DEFAULT_DONGLE_POLL_INTERVAL,
        **_unused: Any,
    ) -> None:
        """Initialize the coordinator. Subclasses consume any extra kwargs."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{serial_number}",
            update_interval=timedelta(seconds=max(int(scan_interval), 1)),
        )
        self.host = protocol.host
        self.protocol = protocol
        self.device_name = device_name
        self.serial_number = serial_number
        self.firmware_version = firmware_version
        self.model = model
        self.laser = LaserInfo()
        self.power_switch_entity_id = power_switch_entity_id
        self.enable_firmware_updates = enable_firmware_updates
        self.firmware_check_interval = max(int(firmware_check_interval), 60)
        self.stats_poll_interval = max(int(stats_poll_interval), 1)
        self.dongle_poll_interval = max(int(dongle_poll_interval), 1)
        self._device_info_fetched = False
        # Counter for the periodic device-info re-fetch. Bumped on every
        # poll that skips the re-fetch; reset back to zero when a
        # re-fetch actually runs. See :meth:`_should_fetch_device_info`.
        self._device_info_refresh_counter = 0

        # mac_address is populated by S1/D-series/REST. F1 V2 leaves it empty.
        # entity.py reads it to add CONNECTION_NETWORK_MAC.
        self.mac_address: str = ""

        # BT-accessory dynamic-add: each per-platform setup_entry stashes its
        # ``async_add_entities`` callback here so :meth:`_dispatch_new_accessories`
        # can register newly-paired accessory entities without a config-entry
        # reload. Set of accessory keys ("<type>:<sn>") we've already built
        # entities for in this session.
        self._platform_callbacks: dict[str, "AddEntitiesCallback"] = {}
        self._known_accessory_keys: set[str] = set()

    # --- Generic helpers ----------------------------------------------------

    @property
    def power_switch_is_off(self) -> bool:
        if not self.power_switch_entity_id:
            return False
        state = self.hass.states.get(self.power_switch_entity_id)
        return state is not None and state.state == "off"

    async def async_shutdown(self) -> None:
        await self.protocol.disconnect()
        await super().async_shutdown()

    def get_status(self) -> XtoolStatus:
        if self.data is None or self.data.status is None:
            return XtoolStatus.UNKNOWN
        return self.data.status

    # Re-run ``_fetch_device_info`` every N polls so a swapped laser
    # module (M116 identity blob), a swapped tool head, or a firmware
    # upgrade surfaces in HA without an integration reload. 60 polls ≈
    # 5 min at the 5 s base interval — cheap (one identity call) and
    # catches all hardware-swap cases, including the ones where the
    # WS / REST connection stayed up across the swap.
    DEVICE_INFO_REFRESH_EVERY_POLLS = 60

    def _should_fetch_device_info(self) -> bool:
        """Return True when the poll loop should re-fetch identity info.

        Returns True on the very first poll (before identity was ever
        fetched) and every ``DEVICE_INFO_REFRESH_EVERY_POLLS`` polls
        after. Bumps the counter on every skipped poll and resets it
        when a re-fetch fires — callers should invoke this exactly
        once per poll cycle and dispatch to ``_fetch_device_info``
        when it returns True.
        """
        if not self._device_info_fetched:
            return True
        if (
            self._device_info_refresh_counter
            >= self.DEVICE_INFO_REFRESH_EVERY_POLLS
        ):
            self._device_info_refresh_counter = 0
            return True
        self._device_info_refresh_counter += 1
        return False

    # --- Polling (per-family override required by HA) -----------------------

    async def _async_update_data(self) -> XtoolDeviceState:
        raise NotImplementedError(
            f"{type(self).__name__} must implement _async_update_data"
        )

    # --- Entity factories (overridden per family) ---------------------------

    def build_switches(self) -> list["SwitchEntity"]:
        return []

    def build_numbers(self) -> list["NumberEntity"]:
        return []

    def build_buttons(self) -> list["ButtonEntity"]:
        return []

    def build_sensors(self) -> list["SensorEntity"]:
        return []

    def build_binary_sensors(self) -> list["BinarySensorEntity"]:
        return []

    def build_lights(self) -> list["LightEntity"]:
        return []

    def build_cameras(self) -> list["Camera"]:
        return []

    def build_fans(self) -> list["FanEntity"]:
        return []

    def build_selects(self) -> list["SelectEntity"]:
        return []

    def build_updates(self) -> list["UpdateEntity"]:
        return []

    def build_events(self) -> list["EventEntity"]:
        return []

    # --- BT accessory polling ----------------------------------------------

    async def _poll_accessories(self, state: XtoolDeviceState) -> None:
        """Refresh ``state.connected_accessories`` from the device.

        Calls the protocol's ``passthrough`` (or ``parts_control``)
        helper with ``M9098`` to enumerate connected accessories per
        the dongle, then per-type ``info_mcode`` to refresh state.
        Gated on ``model.has_bt_accessories``; protocols without the
        passthrough helper silently no-op.
        """
        if not self.model.has_bt_accessories:
            return
        proto = self.protocol
        passthrough = (
            getattr(proto, "parts_control", None)
            or getattr(proto, "passthrough", None)
        )
        if passthrough is None:
            return
        try:
            from .protocols.accessories import (
                ACCESSORY_DEFINITIONS,
                get_definition,
                parse_connected_list,
            )
            from .protocols.accessories.base import (
                MCODE_DONGLE_CONNECTED_LIST,
            )
        except Exception as err:
            _LOGGER.debug("Accessory imports failed: %s", err)
            return
        # Use the Dongle prefix for the M9098 enumeration call — the
        # dongle is the BT bridge, every paired accessory hangs off it.
        dongle = ACCESSORY_DEFINITIONS["Dongle"]
        try:
            reply = await passthrough(
                MCODE_DONGLE_CONNECTED_LIST, dongle.prefix,
            )
        except Exception as err:
            _LOGGER.debug("Accessory M9098 poll failed: %s", err)
            return
        _LOGGER.debug("Accessory M9098 reply: %r", reply)
        if not isinstance(reply, str) or not reply:
            state.connected_accessories = {}
            return
        rows = parse_connected_list(reply)
        _LOGGER.debug(
            "Accessory M9098 parsed %d row(s): %r", len(rows), rows,
        )
        new_state: dict[str, AccessoryState] = {}
        loop_now = asyncio.get_event_loop().time()
        for row in rows:
            type_id = _resolve_type_id(row)
            if type_id is None:
                _LOGGER.debug(
                    "Accessory row skipped — unknown type id "
                    "type_id_raw=%r row=%r",
                    row.get("type_id_raw"), row,
                )
                continue
            definition = get_definition(type_id)
            if definition is None:
                _LOGGER.debug(
                    "Accessory %s skipped — no AccessoryDefinition "
                    "registered (raw=%r)", type_id, row,
                )
                continue
            mac_sn = str(row.get("sn") or row.get("type_id_raw") or "unknown")
            fields: dict[str, Any] = {}
            if definition.info_mcode:
                try:
                    info_reply = await passthrough(
                        definition.info_mcode, definition.prefix,
                    )
                except Exception as err:
                    _LOGGER.debug(
                        "Accessory %s %s poll failed: %s",
                        type_id, definition.info_mcode, err,
                    )
                    info_reply = None
                _LOGGER.debug(
                    "Accessory %s %s reply: %r",
                    type_id, definition.info_mcode, info_reply,
                )
                if isinstance(info_reply, str) and info_reply:
                    try:
                        fields = definition.parse_info(info_reply)
                    except Exception as err:
                        _LOGGER.debug(
                            "Accessory %s parse failed: %s — raw=%r",
                            type_id, err, info_reply,
                        )
            # Prefer the firmware-reported product SN (from the
            # ``info_mcode`` reply, e.g. ``M9082 E:"<sn>"``) over the
            # BT MAC that ``M9098`` emits. MAC contains colons that
            # break HA's entity-id format, and the product SN is
            # what's printed on the accessory's label — far better
            # UX. Fall back to MAC when the info-mcode poll didn't
            # yield an SN (e.g. older DuctFanV1 without that field).
            product_sn = str(fields.get("sn") or "").strip()
            sn = product_sn or mac_sn
            key = f"{type_id}:{sn}"
            # Carry forward sticky fields the poll reply can't
            # populate but push events / set-handlers cache (e.g.
            # DuctFanV3's ``auto_submode`` — Auto Regular vs Quiet
            # is unrecoverable from the M9082 poll, only the
            # write-side knows). Without this, every 5 s poll would
            # wipe the cache and the Select would drift to the
            # default Auto Regular.
            prior = (state.connected_accessories or {}).get(key)
            if prior is not None:
                for sticky in ("auto_submode",):
                    if sticky not in fields and sticky in prior.fields:
                        fields[sticky] = prior.fields[sticky]
            _LOGGER.debug(
                "Accessory %s detected: sn=%s (mac=%s) fields=%r",
                type_id, sn, mac_sn, fields,
            )
            new_state[key] = AccessoryState(
                type_id=type_id, sn=sn, fields=fields,
                last_seen=loop_now,
            )
        state.connected_accessories = new_state

    # --- BT accessory dynamic-add hooks ------------------------------------

    def register_platform_add(
        self, platform: str, callback: "AddEntitiesCallback",
    ) -> None:
        """Stash a platform's ``async_add_entities`` for runtime reuse.

        Called from each top-level ``<platform>.py`` setup_entry in
        addition to its existing one-shot call. The accessory layer
        invokes the stored callback whenever a newly-paired BT
        accessory needs entities added without a config-entry
        reload.
        """
        self._platform_callbacks[platform] = callback

    def initial_accessory_entities(self, platform: str) -> list[Any]:
        """Return accessory entities already present in
        ``connected_accessories`` for one platform.

        Called from each platform's ``async_setup_entry`` so the
        first batch of entities is added in the same call as the
        platform's regular entities. The dispatcher
        (:meth:`_dispatch_new_accessories`) handles every accessory
        that pairs **after** setup; this helper covers the ones
        that were already paired when ``async_config_entry_first_refresh``
        ran. Keys are marked known so the dispatcher doesn't
        duplicate them.
        """
        if not self.data or not self.data.connected_accessories:
            return []
        try:
            from .protocols.accessories.entities import (
                build_accessory_entities,
            )
        except Exception as err:
            _LOGGER.debug("Accessory entity import failed: %s", err)
            return []
        out: list[Any] = []
        for key, acc in self.data.connected_accessories.items():
            entities = build_accessory_entities(self, acc)
            for entity in entities:
                if getattr(entity, "_xtool_platform", None) == platform:
                    out.append(entity)
            self._known_accessory_keys.add(key)
        _LOGGER.debug(
            "Accessory initial %s entities: %d",
            platform, len(out),
        )
        return out

    def _dispatch_new_accessories(self) -> None:
        """Build entities for every connected accessory we haven't
        seen yet this session and dispatch them per platform.

        Idempotent — accessories whose unique-id key is already in
        ``_known_accessory_keys`` are skipped. Accessories that
        disconnect simply leave their entities registered with
        ``available=False``; they re-populate on reconnect because
        the same unique-id key is hit again.
        """
        if not self.data or not self.data.connected_accessories:
            return
        # Sweep stale MAC-keyed accessory devices left over from the
        # v2.5.7 unique-id swap (BT MAC → firmware product SN). Runs
        # on every dispatch but is set-difference idempotent — a
        # no-op once the registry is clean.
        self._cleanup_orphan_accessory_devices()
        try:
            from .protocols.accessories.entities import (
                build_accessory_entities,
            )
        except Exception as err:
            _LOGGER.debug(
                "Accessory entity import failed: %s", err,
            )
            return
        new_per_platform: dict[str, list[Any]] = {}
        for key, acc in self.data.connected_accessories.items():
            if key in self._known_accessory_keys:
                continue
            entities = build_accessory_entities(self, acc)
            if not entities:
                self._known_accessory_keys.add(key)
                continue
            for entity in entities:
                platform = getattr(entity, "_xtool_platform", None)
                if platform is None:
                    continue
                new_per_platform.setdefault(platform, []).append(entity)
            self._known_accessory_keys.add(key)
        for platform, items in new_per_platform.items():
            cb = self._platform_callbacks.get(platform)
            if cb is None:
                _LOGGER.debug(
                    "Accessory %s: no %s platform callback registered",
                    items, platform,
                )
                continue
            _LOGGER.debug(
                "Accessory dispatch: adding %d new %s entities",
                len(items), platform,
            )
            cb(items)

    def _cleanup_orphan_accessory_devices(self) -> None:
        """Remove HA device-registry entries for accessories that were
        keyed by BT MAC before v2.5.7's swap to firmware product SN.

        On upgrade the old MAC-keyed device entries persist alongside
        the new SN-keyed ones — e.g. one IF2 with the full entity
        set (SN-keyed, live) and a phantom IF2 with the leftover
        ``time_the_inline_fan_continues_to_work_2`` / firmware
        entities (MAC-keyed, orphan). Detected by a MAC-shaped tail
        on the device identifier — if that key isn't in the live
        ``connected_accessories`` map, the device is removed.
        Idempotent: once cleared the regex never matches again.
        """
        import re
        from homeassistant.helpers import device_registry as dr

        if not self.data or not self.serial_number:
            return
        live_keys: set[str] = set(self.data.connected_accessories or {})
        laser_sid = self.serial_number
        dev_reg = dr.async_get(self.hass)
        mac_tail = re.compile(
            r":[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$"
        )
        # Snapshot — async_remove_device mutates the registry's dict.
        for device in list(dev_reg.devices.values()):
            for ident in device.identifiers:
                # Defensive: third-party integrations occasionally
                # register 3-tuple identifiers; unpack-by-index keeps
                # the sweep narrow and skips anything that isn't a
                # 2-tuple under our DOMAIN.
                if not isinstance(ident, tuple) or len(ident) != 2:
                    continue
                domain, identifier = ident
                if domain != DOMAIN or not isinstance(identifier, str):
                    continue
                prefix = f"{laser_sid}:"
                if not identifier.startswith(prefix):
                    continue
                if not mac_tail.search(identifier):
                    continue
                acc_key = identifier[len(prefix):]
                if acc_key in live_keys:
                    continue
                _LOGGER.info(
                    "Removing orphaned MAC-keyed accessory device %r — "
                    "superseded by SN-keyed equivalent (v2.5.7+ unique-id swap)",
                    identifier,
                )
                dev_reg.async_remove_device(device.id)
                break

    # --- Event emission -----------------------------------------------------

    def _emit_event(
        self,
        kind: str,
        event_type: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Dispatch an event to every ``XtoolEvent`` entity bound to
        this device.

        Per-family coordinators call this from their poll loop (job /
        error transitions detected from Status edges) or from a
        protocol push-event drain (e.g. V2 button presses). Status →
        event mapping is intentionally left to the family because the
        meaningful transitions differ — S1's M222 codes, REST's
        ``/cnc/status``, V2's ``P_*`` enum each have their own set of
        intermediate states that don't all map 1:1 to the universal
        ``XtoolStatus`` enum.
        """
        async_dispatcher_send(
            self.hass,
            f"xtool_event_{self.serial_number}",
            kind, event_type, attributes or {},
        )
