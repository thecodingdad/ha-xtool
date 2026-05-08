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
        if self.data and self.data.available:
            state = dataclass_replace(self.data)
        else:
            state = XtoolDeviceState()

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

        except Exception as err:
            _LOGGER.debug("WS-V2 poll failed: %s", err)
            state.available = False
            await self.protocol.disconnect()

        return state

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
