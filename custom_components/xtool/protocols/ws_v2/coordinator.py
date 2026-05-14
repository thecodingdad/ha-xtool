"""WS-V2 coordinator — owns the WSV2Protocol connection lifecycle."""

from __future__ import annotations

import logging
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING, Any

from ...coordinator import XtoolCoordinator
from ..base import XtoolDeviceState

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor import BinarySensorEntity
    from homeassistant.components.button import ButtonEntity
    from homeassistant.components.camera import Camera
    from homeassistant.components.event import EventEntity
    from homeassistant.components.light import LightEntity
    from homeassistant.components.number import NumberEntity
    from homeassistant.components.select import SelectEntity
    from homeassistant.components.sensor import SensorEntity
    from homeassistant.components.switch import SwitchEntity
    from homeassistant.components.update import UpdateEntity

_LOGGER = logging.getLogger(__name__)


# Studio's V2 ``ext_purifier`` peripheral speeds map 0..3 to the
# off/low/medium/high labels the accessory's speed-select uses.
_PURIFIER_SPEED_INT_TO_LABEL: dict[int, str] = {
    0: "off", 1: "low", 2: "medium", 3: "high",
}


class WSV2Coordinator(XtoolCoordinator):
    """Coordinator for the WS-V2 family (F1, F1U, F2 family, M1U, P2S, P3,
    MetalFab, Apparel Printer, F1 Lite, F1 Ultra V2 — anything that runs
    V2 firmware).

    Same lifecycle pattern as the legacy REST coordinator: open the
    protocol, periodically poll state into the shared
    ``XtoolDeviceState`` dataclass, hand state to entities through the
    standard HA `_async_update_data` hook.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Forward the model's ``firmware_machine_type`` so the V2 flash
        # handshake sends the right value.
        if hasattr(self.protocol, "set_machine_type"):
            self.protocol.set_machine_type(self.model.firmware_machine_type)
        # Hand the full XtoolDeviceModel to the protocol so its
        # ``poll_state`` can gate per-model peripheral queries (water,
        # gyro, drawer, IR LED, …).
        if hasattr(self.protocol, "set_model"):
            self.protocol.set_model(self.model)

    async def _async_update_data(self) -> XtoolDeviceState:
        # Always carry the last poll's field values forward so the
        # read-only entities (XtoolReadOnlyEntity) keep rendering
        # across a sustained outage. ``state.available`` is flipped
        # to True only on a successful poll below.
        if self.data:
            state = dataclass_replace(self.data)
        else:
            state = XtoolDeviceState()
        state.available = False

        try:
            if not self.protocol.connected:
                await self.protocol.connect()

            # Re-attempt the machineInfo fetch each poll until it
            # actually returns identifying data — MetalFab's GET
            # /v1/device/machineInfo is empty on first call and only
            # populates after the `/device/info MACHINE_INFO INFO`
            # push lands a few hundred ms into the connection.
            if (
                not self._device_info_fetched
                or not self.serial_number
                or not self.firmware_version
            ):
                await self._fetch_device_info()
                if self.serial_number or self.firmware_version:
                    self._device_info_fetched = True

            prev_status = self.data.status if self.data else None
            prev_alarm = self.data.alarm_present if self.data else False

            await self.protocol.poll_state(state)

            state.available = True
            state.device_name = self.device_name
            state.serial_number = self.serial_number
            state.firmware_version = self.firmware_version
            state.laser = self.laser

            self._emit_status_transition_events(prev_status, state)
            self._emit_fire_warning_if_changed(prev_alarm, state.alarm_present)
            self._drain_protocol_events()

            await self._poll_accessories(state)

        except Exception as err:
            _LOGGER.debug("WS-V2 poll failed: %s", err)
            state.available = False
            await self.protocol.disconnect()

        # Set state on coordinator before dispatching new-accessory
        # entities so their ``available`` property reads the fresh data.
        if state.available:
            self.data = state
            self._dispatch_new_accessories()

        return state

    async def _poll_accessories(self, state: XtoolDeviceState) -> None:
        """Run the standard BT-accessory walk, then merge laser-state
        values into the AirPumpV2 / Purifier child-device accessory
        states so the laser-host control entities (air-assist gear
        defaults, close-delay, purifier check / continue / timeout,
        external-purifier speed) surface on the accessory's HA
        child device instead of on the laser itself.

        Per-model gated by ``has_air_assist_state`` / ``has_purifier_timeout``
        — those flags signal "this firmware exposes the laser-host
        endpoints for the accessory". When False (e.g. F2 Ultra UV
        whose AirPump V2 is fully BT-driven), the field merge is
        skipped and the accessory keeps its pure BT-tunneled
        wire surface.
        """
        await super()._poll_accessories(state)
        # Drain any `/accessory/status` push updates queued by the
        # protocol since the last poll. Each entry carries the
        # leading M-code + a dict of parsed fields; we look up the
        # matching accessory in ``connected_accessories`` (keyed by
        # the M-code's owning AccessoryDefinition) and merge fields
        # in. The push lands ahead of the next BT-walk so entity
        # state refreshes between the (slower) 60 s passthrough
        # polls.
        pending = getattr(self.protocol, "_pending_accessory_updates", None)
        if pending:
            updates, self.protocol._pending_accessory_updates = pending, []
            self._merge_accessory_push_updates(state, updates)
        accs = state.connected_accessories or {}
        model = self.model
        for acc in accs.values():
            if (
                acc.type_id in ("AirPump", "AirPumpV2")
                and model.has_air_assist_state
            ):
                # V2 firmware exposes air-pump state through the
                # laser's ``airassistV2`` peripheral push + the
                # device-config endpoints; merge those into the
                # accessory fields so the unified entity surface
                # mirrors what S1 already does.
                acc.fields.setdefault("gear", state.air_assist_level)
                acc.fields["connected"] = bool(state.air_assist_enabled)
                acc.fields["running"] = (
                    bool(state.air_assist_enabled)
                    and (state.air_assist_level or 0) > 0
                )
                acc.fields["close_delay"] = state.air_assist_close_delay
                acc.fields["air_assist_gear_cut"] = state.air_assist_gear_cut
                acc.fields["air_assist_gear_grave"] = (
                    state.air_assist_gear_grave
                )
            elif (
                acc.type_id in ("Purifier", "LargePurifier", "LargePurifierV3")
                and model.has_purifier_timeout
            ):
                acc.fields["purifier_speed"] = _PURIFIER_SPEED_INT_TO_LABEL.get(
                    int(state.purifier_speed or 0), "off",
                )
                if state.purifier_check is not None:
                    acc.fields["purifier_check"] = bool(state.purifier_check)
                if state.purifier_continue is not None:
                    acc.fields["purifier_continue"] = bool(
                        state.purifier_continue
                    )
                acc.fields["purifier_timeout"] = state.purifier_timeout

    def _merge_accessory_push_updates(
        self,
        state: XtoolDeviceState,
        updates: list[tuple[str, dict[str, Any]]],
    ) -> None:
        """Apply pending `/accessory/status` push updates into
        ``state.connected_accessories`` by matching each M-code head
        to the accessory type it belongs to.

        Mapping is conservative — gear-set acks (``M9064``,
        ``M9039``) only update accessory state when an instance of
        the owning type is already paired (we don't synthesise a
        connection from a single push without a prior M9098 walk).
        """
        if not state.connected_accessories:
            return
        # Map M-code head → tuple of type_ids that own it. Mirrors
        # the per-accessory ``info_mcode`` / writer wiring in
        # ``protocols/accessories/*``.
        from ..accessories.base import (
            MCODE_FAN_INFO,
            MCODE_FAN_SET_GEAR,
            MCODE_PURIFIER_INFO,
            MCODE_PURIFIER_SET_GEAR,
        )
        owners: dict[str, tuple[str, ...]] = {
            MCODE_FAN_INFO: ("DuctFan", "DuctFanV3", "AirPump", "AirPumpV2"),
            MCODE_FAN_SET_GEAR: ("DuctFan", "DuctFanV3"),
            MCODE_PURIFIER_INFO: (
                "Purifier", "LargePurifier", "LargePurifierV3",
                "BackpackPurifier",
            ),
            MCODE_PURIFIER_SET_GEAR: (
                "Purifier", "LargePurifier", "LargePurifierV3",
            ),
        }
        for head, fields in updates:
            if not fields:
                continue
            wanted_types = owners.get(head)
            if not wanted_types:
                _LOGGER.debug(
                    "WS-V2 /accessory/status push: no owner mapping for "
                    "M-code %s — dropping fields=%r",
                    head, fields,
                )
                continue
            matched: list[str] = []
            for acc in state.connected_accessories.values():
                if acc.type_id not in wanted_types:
                    continue
                for k, v in fields.items():
                    if v is None:
                        continue
                    acc.fields[k] = v
                matched.append(f"{acc.type_id}:{acc.sn}")
            if matched:
                _LOGGER.debug(
                    "WS-V2 /accessory/status push: merged %s into %s — "
                    "fields=%r",
                    head, ", ".join(matched), fields,
                )
            else:
                _LOGGER.debug(
                    "WS-V2 /accessory/status push: %s landed but no "
                    "paired accessory of type(s) %s — fields dropped "
                    "(fields=%r)",
                    head, wanted_types, fields,
                )

    async def _fetch_device_info(self) -> None:
        try:
            info = await self.protocol.get_device_info()
            if info.serial_number and not self.serial_number:
                self.serial_number = info.serial_number
            if info.laser_power_watts:
                self.laser = info.laser
            if info.main_firmware:
                self.firmware_version = info.main_firmware
            if info.mac_address:
                self.mac_address = info.mac_address
        except Exception as err:
            _LOGGER.debug("WS-V2 device info fetch failed: %s", err)

    # --- Entity builders --------------------------------------------------

    def build_sensors(self) -> list["SensorEntity"]:
        from .entities import build_wsv2_sensors
        return build_wsv2_sensors(self)

    def build_binary_sensors(self) -> list["BinarySensorEntity"]:
        from .entities import build_wsv2_binary_sensors
        return build_wsv2_binary_sensors(self)

    def build_updates(self) -> list["UpdateEntity"]:
        from .entities import build_wsv2_updates
        return build_wsv2_updates(self)

    def build_switches(self) -> list["SwitchEntity"]:
        from .entities import build_wsv2_switches
        return build_wsv2_switches(self)

    def build_numbers(self) -> list["NumberEntity"]:
        from .entities import build_wsv2_numbers
        return build_wsv2_numbers(self)

    def build_selects(self) -> list["SelectEntity"]:
        from .entities import build_wsv2_selects
        return build_wsv2_selects(self)

    def build_lights(self) -> list["LightEntity"]:
        from .entities import build_wsv2_lights
        return build_wsv2_lights(self)

    def build_buttons(self) -> list["ButtonEntity"]:
        from .entities import build_wsv2_buttons
        return build_wsv2_buttons(self)

    def build_cameras(self) -> list["Camera"]:
        from .entities import build_wsv2_cameras
        return build_wsv2_cameras(self)

    def build_events(self) -> list["EventEntity"]:
        from .entities import build_wsv2_events
        return build_wsv2_events(self)

    # --- Event emission (V2-specific transitions + push drain) ----------

    def _emit_status_transition_events(
        self,
        prev_status: "Any",
        state: XtoolDeviceState,
    ) -> None:
        """Detect job + error transitions on the V2 firmware Status
        edge. The mapping intentionally mirrors how V2 firmware drives
        ``XtoolStatus`` via the ``P_*`` codes consumed in
        ``WSV2_MODE_MAP``.
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

        # Job lifecycle
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

        # Framing — orthogonal to job lifecycle
        if prev_status not in framing and new_status in framing:
            self._emit_event("job", "framing_started", None)
        if prev_status in framing and new_status not in framing:
            self._emit_event("job", "framing_finished", None)

        # Errors — fire on every entry into an ERROR_* state. The
        # ``emergency_stop`` sub-event is push-driven (see protocol
        # ``_dispatch_push`` for ``/emergency/status``).
        if new_status in errors:
            self._emit_event("error", errors[new_status], None)

    def _emit_fire_warning_if_changed(
        self, prev: bool, new: bool,
    ) -> None:
        """Edge detector for ``state.alarm_present`` on V2.

        On WS-V2 the ``/v1/device/alarms`` list is generic — we
        cannot distinguish fire from a non-fire alarm without an
        alarm-type subfield. The dedicated ``emergency_stop`` push
        already routes to the ``error`` event, so this catch-all is
        the closest fire-specific signal V2 currently offers.
        """
        if bool(prev) == bool(new):
            return
        self._emit_event(
            "fire_warning",
            "triggered" if new else "cleared",
            None,
        )

    def _drain_protocol_events(self) -> None:
        """Forward V2 push events the protocol queued during the WS
        reader loop into HA's dispatcher.

        Currently surfaces:

        - Button presses pushed on ``/button/status BUTTON``.
        - Emergency-stop transitions on ``/emergency/status``.

        The protocol stores them in ``protocol._pending_events`` —
        each entry is a ``(kind, event_type, attrs)`` tuple. The
        coordinator drains the list every poll under the event-loop
        thread so ``async_dispatcher_send`` is safe to call.
        """
        pending = getattr(self.protocol, "_pending_events", None)
        if not pending:
            return
        # Atomically swap so concurrent push handlers don't lose events.
        events, self.protocol._pending_events = pending, []
        for kind, event_type, attrs in events:
            self._emit_event(kind, event_type, attrs)
