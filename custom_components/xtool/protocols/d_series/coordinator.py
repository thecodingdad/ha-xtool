"""D-series family coordinator — REST + WS push."""

from __future__ import annotations

import logging
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING, Any

from ...coordinator import XtoolCoordinator
from ..base import XtoolDeviceState

if TYPE_CHECKING:
    from homeassistant.components.button import ButtonEntity
    from homeassistant.components.event import EventEntity
    from homeassistant.components.number import NumberEntity
    from homeassistant.components.select import SelectEntity
    from homeassistant.components.sensor import SensorEntity
    from homeassistant.components.switch import SwitchEntity
    from homeassistant.components.update import UpdateEntity

_LOGGER = logging.getLogger(__name__)


class DSeriesCoordinator(XtoolCoordinator):
    """Coordinator for the xTool D1 / D1 Pro / D1 Pro 2.0."""

    async def _async_update_data(self) -> XtoolDeviceState:
        if self.data and self.data.available:
            state = dataclass_replace(self.data)
        else:
            state = XtoolDeviceState()

        try:
            if not self.protocol.connected:
                await self.protocol.connect()

            if not self._device_info_fetched:
                await self._fetch_device_info()
                self._device_info_fetched = True

            prev_status = self.data.status if self.data else None

            await self.protocol.poll_state(state)

            state.available = True
            state.device_name = self.device_name
            state.serial_number = self.serial_number
            state.firmware_version = self.firmware_version
            state.laser = self.laser

            self._emit_status_transition_events(prev_status, state)
            self._emit_fire_warning_if_status_changed(
                prev_status, state.status,
            )

            await self._poll_accessories(state)

        except Exception as err:
            _LOGGER.debug("Error polling xTool D-series: %s", err)
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
            if info.mac_address:
                self.mac_address = info.mac_address
        except Exception as err:
            _LOGGER.debug("Failed to fetch D-series device info: %s", err)

    # --- Entity builders ----------------------------------------------------

    def build_switches(self) -> list["SwitchEntity"]:
        from .entities import build_d_series_switches
        return build_d_series_switches(self)

    def build_numbers(self) -> list["NumberEntity"]:
        from .entities import build_d_series_numbers
        return build_d_series_numbers(self)

    def build_buttons(self) -> list["ButtonEntity"]:
        from .entities import build_d_series_buttons
        return build_d_series_buttons(self)

    def build_selects(self) -> list["SelectEntity"]:
        from .entities import build_d_series_selects
        return build_d_series_selects(self)

    def build_sensors(self) -> list["SensorEntity"]:
        from .entities import build_d_series_sensors
        return build_d_series_sensors(self)

    def build_updates(self) -> list["UpdateEntity"]:
        from .entities import build_d_series_updates
        return build_d_series_updates(self)

    def build_events(self) -> list["EventEntity"]:
        from .entities import build_d_series_events
        return build_d_series_events(self)

    # --- Event emission (D-series transitions) --------------------------

    def _emit_status_transition_events(
        self,
        prev_status: Any,
        state: XtoolDeviceState,
    ) -> None:
        """Job + error events on D-series Status edges."""
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
        }
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
                "job", "finished", {"task_id": state.task_id},
            )
        elif new_status == XtoolStatus.CANCELLING:
            self._emit_event("job", "cancelled", {"task_id": state.task_id})

        if new_status in errors:
            self._emit_event("error", errors[new_status], None)

    def _emit_fire_warning_if_status_changed(
        self, prev_status: Any, new_status: Any,
    ) -> None:
        """D-series flame-detector edge — derived from the
        ``ERROR_FIRE_WARNING`` Status enum value (mapped from
        ``err:flameCheck`` in ``DSERIES_WS_EVENT_MAP``).
        """
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
