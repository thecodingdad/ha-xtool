"""REST family coordinator — pure HTTP polling."""

from __future__ import annotations

import logging
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING, Any

import time

import aiohttp
from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.storage import Store

from ...coordinator import XtoolCoordinator
from ..base import XtoolDeviceState

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor import BinarySensorEntity
    from homeassistant.components.button import ButtonEntity
    from homeassistant.components.camera import Camera
    from homeassistant.components.event import EventEntity
    from homeassistant.components.light import LightEntity
    from homeassistant.components.lock import LockEntity
    from homeassistant.components.number import NumberEntity
    from homeassistant.components.select import SelectEntity
    from homeassistant.components.sensor import SensorEntity
    from homeassistant.components.switch import SwitchEntity
    from homeassistant.components.update import UpdateEntity

_LOGGER = logging.getLogger(__name__)


class RestCoordinator(XtoolCoordinator):
    """Coordinator for F1, F1 Ultra, P1, P2, P2S, M1, M1 Ultra, GS005."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # REST flashing on P2/P2S/M1Ultra needs a machine_type query param.
        # The value is constant per model — set once at init so the protocol
        # carries it through every flash without needing a runtime hook.
        self.protocol.set_machine_type(self.model.firmware_machine_type)
        self.protocol.set_strategy(self.model.firmware_flash_strategy)
        # Stash model on the protocol so capability-gated polls can fire.
        self.protocol.set_model(self.model)

    async def _async_update_data(self) -> XtoolDeviceState:
        # Carry the last poll's field values forward so the read-only
        # entities (XtoolReadOnlyEntity) keep rendering across a
        # sustained outage. ``state.available`` flips to True only on
        # a successful poll below.
        if self.data:
            state = dataclass_replace(self.data)
        else:
            state = XtoolDeviceState()
        state.available = False

        try:
            if not self.protocol.connected:
                await self.protocol.connect()

            if self._should_fetch_device_info():
                await self._fetch_device_info()
                self._device_info_fetched = True

            prev_status = self.data.status if self.data else None
            prev_button = (
                self.data.last_button_event if self.data else ""
            )

            await self.protocol.poll_state(state)

            state.available = True
            state.device_name = self.device_name
            state.serial_number = self.serial_number
            state.firmware_version = self.firmware_version
            state.laser = self.laser

            # Fallback: if /gethardwaretype returned nothing useful,
            # use the board revision parsed from /device/machineInfo.
            if not state.hardware_type and getattr(self, "_hardware_version", ""):
                state.hardware_type = self._hardware_version

            self._emit_status_transition_events(prev_status, state)
            self._emit_button_event_if_changed(prev_button, state.last_button_event)
            self._emit_fire_warning_if_status_changed(prev_status, state.status)

            await self._poll_accessories(state)

        except Exception as err:
            _LOGGER.debug("Error polling xTool REST device: %s", err)
            state.available = False
            await self.protocol.disconnect()

        if state.available:
            self.data = state
            self._dispatch_new_accessories()
        return state

    async def _fetch_device_info(self) -> None:
        try:
            info = await self.protocol.get_device_info()
            if info.serial_number and not self.serial_number:
                self.serial_number = info.serial_number
            if info.laser.power_watts:
                self.laser = info.laser
            if info.main_firmware:
                self.firmware_version = info.main_firmware
            if info.hardware_version:
                self._hardware_version = info.hardware_version
            if info.mac_address:
                self.mac_address = info.mac_address
        except Exception as err:
            _LOGGER.debug("Failed to fetch REST device info: %s", err)

    # --- Entity builders ----------------------------------------------------

    def build_switches(self) -> list["SwitchEntity"]:
        from .entities import build_rest_switches
        return build_rest_switches(self)

    def build_numbers(self) -> list["NumberEntity"]:
        from .entities import build_rest_numbers
        return build_rest_numbers(self)

    def build_buttons(self) -> list["ButtonEntity"]:
        from .entities import build_rest_buttons
        return build_rest_buttons(self)

    def build_lights(self) -> list["LightEntity"]:
        from .entities import build_rest_lights
        return build_rest_lights(self)

    def build_cameras(self) -> list["Camera"]:
        from .entities import build_rest_cameras
        return build_rest_cameras(self)

    def build_sensors(self) -> list["SensorEntity"]:
        from .entities import build_rest_sensors
        return build_rest_sensors(self)

    def build_binary_sensors(self) -> list["BinarySensorEntity"]:
        from .entities import build_rest_binary_sensors
        return build_rest_binary_sensors(self)

    def build_selects(self) -> list["SelectEntity"]:
        from .entities import build_rest_selects
        return build_rest_selects(self)

    def build_updates(self) -> list["UpdateEntity"]:
        from .entities import build_rest_updates
        return build_rest_updates(self)

    def build_events(self) -> list["EventEntity"]:
        from .entities import build_rest_events
        return build_rest_events(self)

    # --- Event emission (REST-specific transitions + button diff) -------

    def _emit_status_transition_events(
        self,
        prev_status: Any,
        state: XtoolDeviceState,
    ) -> None:
        """Job + error events on REST status edges. The mapping
        mirrors the WS-V2 coordinator's logic — REST and V2 firmware
        normalise to the same ``XtoolStatus`` enum so the transitions
        play out identically once status is resolved.
        """
        from ...const import XtoolStatus

        new_status = state.status
        if new_status is None or new_status == prev_status:
            return

        running = {
            XtoolStatus.PROCESSING,
            XtoolStatus.WORKING_API,
            XtoolStatus.WORKING_BUTTON,
        }
        idle_like = {
            XtoolStatus.OFF,
            XtoolStatus.IDLE,
            XtoolStatus.SLEEPING,
            XtoolStatus.INITIALIZING,
            XtoolStatus.PROCESSING_READY,
        }
        framing = {XtoolStatus.FRAMING}
        errors = {
            XtoolStatus.ERROR_LIMIT:         "limit",
            XtoolStatus.ERROR_LASER_CONTROL: "laser_control",
            XtoolStatus.ERROR_LASER_MODULE:  "laser_module",
            XtoolStatus.ERROR_TILT:          "tilt",
            XtoolStatus.ERROR_MOVING:        "moving",
        }

        if prev_status in idle_like and new_status in running:
            self._emit_event(
                "job", "started",
                {"task_id": state.task_id} if state.task_id else None,
            )
        elif prev_status in running and new_status == XtoolStatus.PAUSED:
            self._emit_event("job", "paused", {"task_id": state.task_id})
        elif prev_status == XtoolStatus.PAUSED and new_status in running:
            self._emit_event("job", "resumed", {"task_id": state.task_id})
        elif (
            prev_status in (running | {XtoolStatus.PAUSED})
            and new_status == XtoolStatus.FINISHED
        ):
            self._emit_event(
                "job", "finished",
                {
                    "task_id": state.task_id,
                    "duration": state.last_job_time_seconds or state.task_time,
                },
            )
        elif new_status == XtoolStatus.CANCELLING:
            self._emit_event("job", "cancelled", {"task_id": state.task_id})

        if prev_status not in framing and new_status in framing:
            self._emit_event("job", "framing_started", None)
        if prev_status in framing and new_status not in framing:
            self._emit_event("job", "framing_finished", None)

        if new_status in errors:
            self._emit_event("error", errors[new_status], None)

    def _emit_button_event_if_changed(
        self, prev: str, new: str,
    ) -> None:
        """REST polls ``/peripheral/button?action=get`` once per cycle —
        emit a button event when the reported event string changes.

        REST firmware reports raw labels like ``"SHORT_PRESS"``,
        ``"LONG_PRESS"`` and (HJ003 typo, see issue #3)
        ``"SHOERT_PRESS"``. The same normalisation map used by WS-V2
        keeps the entity vocabulary stable across protocols.
        """
        if not new or new == prev:
            return
        from .protocol import _normalise_rest_button_event

        normalised = _normalise_rest_button_event(new)
        if normalised:
            self._emit_event(
                "button", normalised,
                {"raw_type": new},
            )

    def _emit_fire_warning_if_status_changed(
        self, prev_status: Any, new_status: Any,
    ) -> None:
        """Edge detector for REST ``ERROR_FIRE_WARNING`` status."""
        from ...const import XtoolStatus

        prev_fire = prev_status == XtoolStatus.ERROR_FIRE_WARNING
        new_fire = new_status == XtoolStatus.ERROR_FIRE_WARNING
        if prev_fire == new_fire:
            return
        self._emit_event(
            "fire_warning",
            "triggered" if new_fire else "cleared",
            None,
        )


class P2Coordinator(RestCoordinator):
    """The P2: polls like the REST family and follows Studio's push channel.

    A push updates the entities at once; the poll every few seconds still
    reads everything. When the push channel closes or goes quiet, the laser
    shows unavailable at once instead of keeping its last values.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.protocol._push_notify = self._on_push
        # Error codes already sent as events from a push
        self._announced: set[str] = set()
        # The laser's addresses seen so far (Wi-Fi and Ethernet), kept
        # across restarts; when the configured one stops answering, the
        # others are tried
        self._hosts_store: Store | None = None
        self._known_hosts: list[str] = []
        self._next_host_search = 0.0
        self._own_entry_update: tuple[dict[str, Any], dict[str, Any]] | None = None

    # --- Identity and addresses ------------------------------------------

    async def _fetch_device_info(self) -> None:
        await super()._fetch_device_info()
        protocol = self.protocol
        self.model_variant = protocol.model_variant() or self.model_variant
        mac = await self.hass.async_add_executor_job(_arp_mac, protocol.host)
        if mac and format_mac(mac) != format_mac(self.mac_address or ""):
            self.other_mac_addresses.add(format_mac(mac))
        await self._remember_hosts([protocol.host, *protocol.addresses()])
        await self._prefer_ethernet()

    async def _prefer_ethernet(self) -> None:
        """Move to the Ethernet address when a cable is connected and it answers."""
        ethernet = self.protocol.ethernet_address()
        if not ethernet or ethernet == self.protocol.host or not self.config_entry:
            return
        if await _serial_at(ethernet) != self.serial_number:
            return
        _LOGGER.info("%s answers on Ethernet at %s; switching from %s",
                     self.device_name, ethernet, self.protocol.host)
        await self._move_to(ethernet)

    async def _move_to(self, host: str) -> None:
        """Use the new address at once and save it, without reloading the entry."""
        entry = self.config_entry
        await self.protocol.change_host(host)
        self.host = host
        data = {**entry.data, CONF_HOST: host}
        # The update listener skips this change: nothing needs a reload
        self._own_entry_update = (data, dict(entry.options))
        self.hass.config_entries.async_update_entry(entry, data=data)

    def made_entry_update(self, entry: Any) -> bool:
        """True when the entry holds exactly what this coordinator wrote to it."""
        return self._own_entry_update == (dict(entry.data), dict(entry.options))

    def _store(self) -> Store:
        if self._hosts_store is None:
            entry_id = self.config_entry.entry_id if self.config_entry else self.serial_number
            self._hosts_store = Store(self.hass, 1, f"xtool.{entry_id}.hosts")
        return self._hosts_store

    async def _load_hosts(self) -> list[str]:
        if not self._known_hosts:
            stored = await self._store().async_load()
            if isinstance(stored, dict) and isinstance(stored.get("hosts"), list):
                self._known_hosts = [str(h) for h in stored["hosts"]]
        return self._known_hosts

    async def _remember_hosts(self, hosts: list[str]) -> None:
        known = await self._load_hosts()
        new = [h for h in dict.fromkeys(hosts) if h and h not in known]
        if new:
            self._known_hosts = [*known, *new]
            await self._store().async_save({"hosts": self._known_hosts})

    async def _find_on_other_address(self) -> None:
        """When the laser stops answering, look for it at its other address."""
        now = time.monotonic()
        if now < self._next_host_search or self.power_switch_is_off or not self.config_entry:
            return
        self._next_host_search = now + HOST_SEARCH_SECONDS
        current = self.protocol.host
        for host in await self._load_hosts():
            if host == current:
                continue
            serial = await _serial_at(host)
            if serial and serial == self.serial_number:
                _LOGGER.info("%s answers at %s instead of %s; switching", self.device_name, host, current)
                await self._move_to(host)
                return

    def _on_push(self) -> None:
        if self.data is None:
            return
        state = dataclass_replace(self.data)
        if not self.protocol.connected:
            if not state.available:
                return
            state.available = False
            self.data = state
            self.async_update_listeners()
            return
        if not state.available:
            # Back after an outage: the next poll reads everything again
            self.hass.async_create_task(self.async_request_refresh())
            return
        prev_status = state.status
        self.protocol.apply_latest(state)
        self.data = state
        self._emit_status_transition_events(prev_status, state)
        self._emit_fire_warning_if_status_changed(prev_status, state.status)
        self._emit_push_events()
        # Not async_set_updated_data: that would move the next poll
        self.async_update_listeners()

    def _emit_push_events(self) -> None:
        for kind, event_type, attributes in self.protocol.take_push_events():
            if kind == "error" and attributes.get("code"):
                self._announced.add(attributes["code"])
            self._emit_event(kind, event_type, attributes)

    async def _async_update_data(self) -> XtoolDeviceState:
        prev_alarms = list(self.data.active_alarms) if self.data else []
        state = await super()._async_update_data()
        self._emit_push_events()
        if state.available:
            self._emit_alarm_changes(prev_alarms, state.active_alarms)
        else:
            await self._find_on_other_address()
        return state

    def _emit_alarm_changes(
        self, prev: list[dict[str, Any]], new: list[dict[str, Any]],
    ) -> None:
        """Events for alarms that appeared since the last poll (and were not pushed)."""

        def key(alarm: dict[str, Any]) -> str:
            return str(alarm.get("code") or alarm.get("message") or "")

        before = {key(a) for a in prev}
        now = {key(a) for a in new}
        for alarm in new:
            if key(alarm) in before or key(alarm) in self._announced:
                continue
            if alarm.get("event_type") == "fire":
                self._emit_event("fire_warning", "triggered", {"level": alarm.get("level")})
                continue
            self._emit_event("error", alarm.get("event_type") or "other", {
                "code": alarm.get("code"),
                "message": alarm.get("message"),
                "level": alarm.get("level"),
            })
        fire_before = any(a.get("event_type") == "fire" for a in prev)
        fire_now = any(a.get("event_type") == "fire" for a in new)
        if fire_before and not fire_now:
            self._emit_event("fire_warning", "cleared", None)
        self._announced.intersection_update(now)

    def build_sensors(self) -> list["SensorEntity"]:
        from .entities import build_p2_sensors
        from .p2_entities import P2Connection, P2MeasuredThickness
        return [*build_p2_sensors(self), P2Connection(self), P2MeasuredThickness(self)]

    def build_switches(self) -> list["SwitchEntity"]:
        from .p2_entities import build_p2_switches
        return build_p2_switches(self)

    def build_locks(self) -> list["LockEntity"]:
        from .p2_entities import build_p2_locks
        return build_p2_locks(self)

    def build_numbers(self) -> list["NumberEntity"]:
        from .p2_entities import build_p2_numbers
        return build_p2_numbers(self)

    def build_selects(self) -> list["SelectEntity"]:
        from .p2_entities import build_p2_selects
        return build_p2_selects(self)

    def build_lights(self) -> list["LightEntity"]:
        from .p2_entities import build_p2_lights
        return build_p2_lights(self)

    def build_cameras(self) -> list["Camera"]:
        from .p2_entities import build_p2_cameras
        return build_p2_cameras(self)

    def build_binary_sensors(self) -> list["BinarySensorEntity"]:
        from .p2_entities import build_p2_binary_sensors
        return build_p2_binary_sensors(self)

    def build_buttons(self) -> list["ButtonEntity"]:
        from .p2_entities import build_p2_buttons
        return build_p2_buttons(self)

    def build_events(self) -> list["EventEntity"]:
        from .entities import build_p2_events
        return build_p2_events(self)


# How often a P2 that stopped answering is looked for at its other address
HOST_SEARCH_SECONDS = 30.0


def _arp_mac(host: str) -> str:
    """The MAC behind ``host`` in the system's ARP table (Linux), or ""."""
    try:
        with open("/proc/net/arp", encoding="ascii") as arp:
            for line in arp.read().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 4 and parts[0] == host and parts[3] != "00:00:00:00:00:00":
                    return parts[3]
    except OSError:
        pass
    return ""


async def _serial_at(host: str) -> str:
    """The serial number of the laser answering at ``host``, or ""."""
    try:
        async with aiohttp.ClientSession() as session, session.get(
            f"http://{host}:8080/device/machineInfo", timeout=aiohttp.ClientTimeout(total=3),
        ) as resp:
            data = await resp.json(content_type=None)
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return str(data["data"].get("sn") or "")
    return ""
