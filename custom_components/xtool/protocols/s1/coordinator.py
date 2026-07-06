"""S1 family coordinator — AP2, XCS, multi-board firmware, workspace dims."""

from __future__ import annotations

import logging
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING, Any

from ...coordinator import XtoolCoordinator
from ..base import XtoolDeviceState

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor import BinarySensorEntity
    from homeassistant.components.button import ButtonEntity
    from homeassistant.components.event import EventEntity
    from homeassistant.components.light import LightEntity
    from homeassistant.components.number import NumberEntity
    from homeassistant.components.select import SelectEntity
    from homeassistant.components.sensor import SensorEntity
    from homeassistant.components.switch import SwitchEntity
    from homeassistant.components.update import UpdateEntity

_LOGGER = logging.getLogger(__name__)


# S1's M1098 reply carries a fixed-position firmware-version array
# per directly-wired accessory slot (USB / serial). The framework
# surfaces each non-empty slot as its own ``AccessoryState`` so the
# user gets a child device per accessory instead of a single
# aggregate binary sensor. Slot 0 (Purifier) is reserved for the BT
# AP2 path — handled separately so its richer field set wins.
_M1098_SLOT_TO_TYPE: dict[int, str] = {
    1: "FireExtinguisher",
    2: "AirPump",
    3: "AirPumpV2",
    4: "FireExtinguisherV1_5",
}


class S1Coordinator(XtoolCoordinator):
    """Coordinator for the xTool S1 (WebSocket M-code protocol)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # S1-only firmware/workspace fields populated by _fetch_device_info().
        self.laser_firmware: str = ""
        self.wifi_firmware: str = ""
        self.workspace_x: float = 0.0
        self.workspace_y: float = 0.0
        self.workspace_z: float = 0.0
        # Forward poll intervals; protocol's defaults match const.py defaults.
        # AP2 polling is now driven by the generic accessory framework
        # (M9098 → per-accessory info M-codes) on the base coordinator's
        # standard cadence — no S1-specific knob.
        self.protocol.set_poll_intervals(
            stats=self.stats_poll_interval,
            dongle=self.dongle_poll_interval,
        )

    @property
    def xcs_compatibility_mode(self) -> bool:
        return self.protocol.xcs_compatibility_mode

    async def _poll_accessories(self, state: XtoolDeviceState) -> None:
        """S1-specific accessory enumeration.

        S1 firmware doesn't expose the F0F7 ``/passthrough`` tunnel
        the REST / D-series / WS-V2 families use (the route returns
        404 — see PROTOCOL.md). Accessory data on S1 comes from two
        always-available native sources:

        - ``M1098`` slot array → per-slot firmware version for
          directly-wired accessories (AirPump, FireExtinguisher,
          AP2 …); the slot walk below adds an
          :class:`AccessoryState` per non-empty slot.
        - ``M9039`` push frames → live AP2 state cached on
          :attr:`S1Protocol._ap2_state`; synthesised here into a
          ``Purifier`` accessory so the AP2 entities (gear / 5
          filter wear / running …) come up via the generic
          framework.

        No M9098 walk — S1's M9098 raw reply is a MAC list with no
        type discriminator (Studio resolves type via a separate
        ``/v1/project/accessory/list`` cloud query the integration
        deliberately doesn't reach for).
        """
        from ..base import AccessoryState

        proto = self.protocol
        new_state: dict[str, AccessoryState] = {}

        # AP2 push-driven state (M9039) → unified Purifier accessory.
        ap2 = getattr(proto, "_ap2_state", None)
        if ap2:
            fields: dict[str, Any] = {
                "gear": ap2.get("purifier_speed"),
                "running": ap2.get("purifier_on"),
            }
            for key in (
                "filter_pre", "filter_medium", "filter_carbon",
                "filter_dense_carbon", "filter_hepa",
                "purifier_sensor_d", "purifier_sensor_s",
            ):
                if key in ap2:
                    fields[key] = ap2[key]
            new_state["Purifier:ap2"] = AccessoryState(
                type_id="Purifier", sn="ap2", fields=fields,
            )

        # M1098 walk — surfaces directly-wired (USB / serial)
        # accessories that don't go through the BT dongle.
        # Firmware-version-only; the M9098 walk above wins for any
        # type that already populated a richer entry.
        accessories_raw = getattr(state, "accessories_raw", None) or []
        _LOGGER.debug(
            "S1 M1098 accessories_raw=%r", accessories_raw,
        )
        for idx, type_id in _M1098_SLOT_TO_TYPE.items():
            if idx >= len(accessories_raw):
                continue
            firmware = accessories_raw[idx]
            if not firmware:
                continue
            if any(k.startswith(f"{type_id}:") for k in new_state):
                continue
            fields: dict[str, Any] = {"version": firmware}
            # AirPump on S1: air-assist live state lives on the
            # laser's M15 push + M1099 poll, not on a BT-tunneled
            # M9082 info call. Merge those fields into the accessory
            # state so the AirPump child device surfaces gear /
            # running / close_delay alongside the firmware version.
            if type_id in ("AirPump", "AirPumpV2"):
                # M15 wire shape: ``A<enabled> S<gear>``. ``A=1``
                # means the air-assist hardware is connected /
                # circuit armed; ``S`` is the gear (0 = inactive,
                # 1-3 = pumping). ``running`` should fire only when
                # the pump is actively pushing air — both flags
                # set; otherwise users see "on" with gear 0 even
                # though no air flows.
                gear = state.air_assist_level
                if gear is not None:
                    fields["gear"] = gear
                fields["connected"] = bool(state.air_assist_enabled)
                fields["running"] = (
                    bool(state.air_assist_enabled)
                    and gear is not None
                    and gear > 0
                )
                if state.air_assist_close_delay is not None:
                    fields["close_delay"] = state.air_assist_close_delay
            _LOGGER.debug(
                "S1 accessory detected (M1098 slot %d): %s fields=%r",
                idx, type_id, fields,
            )
            new_state[f"{type_id}:slot{idx}"] = AccessoryState(
                type_id=type_id, sn=f"slot{idx}", fields=fields,
            )

        # M9033 (Purifier info) / M9082 (DuctFan / AirPump info)
        # raw-WS poll — gap-fill for accessories whose M1098 slot
        # only carries a firmware-version string. Studio's S1 bundle
        # sends both M-codes over the WS, so the firmware does
        # accept them on raw WS (previous "passthrough only"
        # assumption was wrong). AirPump still wins via the laser-
        # host M15 path because the M9082 reply only carries
        # ``A0`` for the air pump.
        await self._poll_s1_accessory_info(new_state)

        state.connected_accessories = new_state

    async def _poll_s1_accessory_info(
        self, accessories: dict[str, "AccessoryState"],
    ) -> None:
        """Send ``M9082`` / ``M9033`` over raw WS to refresh paired
        DuctFan / Purifier accessories' state fields.

        Best-effort — failures are logged at DEBUG and the
        accessory keeps whatever fields it already has.
        """
        from .. import accessories as _acc_module
        proto = self.protocol
        for key, acc in accessories.items():
            definition = _acc_module.get_definition(acc.type_id)
            if definition is None or not definition.info_mcode:
                continue
            if definition.parse_info is None:
                continue
            # AirPump info comes from the laser-host M15 / M1099
            # path on S1 (filled above) — skip the redundant M9082
            # round-trip.
            if acc.type_id in ("AirPump", "AirPumpV2"):
                continue
            try:
                reply = await proto.send_command(
                    definition.info_mcode, timeout=3.0,
                )
            except Exception as err:
                _LOGGER.debug(
                    "S1 %s info poll failed (%s): %s",
                    acc.type_id, definition.info_mcode, err,
                )
                continue
            if not reply:
                continue
            try:
                parsed = definition.parse_info(reply)
            except Exception as err:
                _LOGGER.debug(
                    "S1 %s info parse failed (raw=%r): %s",
                    acc.type_id, reply, err,
                )
                continue
            if not parsed:
                continue
            _LOGGER.debug(
                "S1 %s info refreshed via %s: %r",
                acc.type_id, definition.info_mcode, parsed,
            )
            for k, v in parsed.items():
                if v is None:
                    continue
                acc.fields[k] = v

    async def send_command(self, command: str) -> str:
        """Send an M-code command to the S1 protocol with safe error logging."""
        try:
            return await self.protocol.send_command(command)
        except Exception as err:
            _LOGGER.warning("Failed to send command %s: %s", command, err)
            return ""

    # --- Polling ------------------------------------------------------------

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
            # In XCS mode, don't try to reconnect WS — poll_state handles it.
            if not self.xcs_compatibility_mode and not self.protocol.connected:
                await self.protocol.connect()

            if self._should_fetch_device_info():
                await self._fetch_device_info()
                self._device_info_fetched = True

            prev_status = self.data.status if self.data else None
            prev_alarm = self.data.alarm_present if self.data else False

            await self.protocol.poll_state(state)

            state.available = True
            state.device_name = self.device_name
            state.serial_number = self.serial_number
            state.firmware_version = self.firmware_version
            state.laser = self.laser

            try:
                state.connection_count = await self.protocol.get_connection_count()
            except Exception:
                pass

            self._emit_status_transition_events(prev_status, state)
            self._emit_fire_warning_if_changed(prev_alarm, state.alarm_present)

            await self._poll_accessories(state)

        except Exception as err:
            _LOGGER.debug("Error polling xTool S1: %s", err)
            # XCS Compatibility Mode — keep cached state on transient errors.
            if self.xcs_compatibility_mode and self.data and self.data.available:
                _LOGGER.debug("XCS Compatibility Mode — keeping cached state")
                return self.data
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
            # Authoritative main firmware comes from M99 (in M2003 JSON).
            # All three board firmwares must be guarded: when the
            # device is powered off, M2003 still parses (returns an
            # empty DeviceInfo) and would otherwise wipe the cached
            # versions, leaving the Update entity with only Main
            # populated → installed-vs-latest mismatch → spurious
            # "update available" against a stale Main version pulled
            # from the multi-package API call.
            if info.main_firmware:
                self.firmware_version = info.main_firmware
            if info.laser_firmware:
                self.laser_firmware = info.laser_firmware
            if info.wifi_firmware:
                self.wifi_firmware = info.wifi_firmware
            if info.mac_address:
                self.mac_address = info.mac_address
            if info.workspace_x:
                self.workspace_x = info.workspace_x
                self.workspace_y = info.workspace_y
                self.workspace_z = info.workspace_z
            self._device_info_cache = info
        except Exception as err:
            _LOGGER.debug("Failed to fetch S1 device info: %s", err)

    # --- Entity builders ----------------------------------------------------

    def build_switches(self) -> list["SwitchEntity"]:
        from .entities import build_s1_switches
        return build_s1_switches(self)

    def build_numbers(self) -> list["NumberEntity"]:
        from .entities import build_s1_numbers
        return build_s1_numbers(self)

    def build_buttons(self) -> list["ButtonEntity"]:
        from .entities import build_s1_buttons
        return build_s1_buttons(self)

    def build_lights(self) -> list["LightEntity"]:
        from .entities import build_s1_lights
        return build_s1_lights(self)

    def build_binary_sensors(self) -> list["BinarySensorEntity"]:
        from .entities import build_s1_binary_sensors
        return build_s1_binary_sensors(self)

    def build_sensors(self) -> list["SensorEntity"]:
        from .entities import build_s1_sensors
        return build_s1_sensors(self)

    def build_updates(self) -> list["UpdateEntity"]:
        from .entities import build_s1_updates
        return build_s1_updates(self)

    def build_selects(self) -> list["SelectEntity"]:
        from .entities import build_s1_selects
        return build_s1_selects(self)

    def build_events(self) -> list["EventEntity"]:
        from .entities import build_s1_events
        return build_s1_events(self)

    # --- Event emission (S1-specific transitions) -----------------------

    def _emit_status_transition_events(
        self,
        prev_status: Any,
        state: XtoolDeviceState,
    ) -> None:
        """Job + error events on S1 Status edges. Mirrors the WS-V2
        and REST mappings — once M222 status codes are normalised to
        the universal ``XtoolStatus`` enum the transitions are
        identical."""
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

    def _emit_fire_warning_if_changed(
        self, prev: bool, new: bool,
    ) -> None:
        """Edge detector for ``state.alarm_present`` (M340) — emits
        the dedicated ``fire_warning`` event when the flame detector
        trips or recovers."""
        if bool(prev) == bool(new):
            return
        self._emit_event(
            "fire_warning",
            "triggered" if new else "cleared",
            None,
        )
