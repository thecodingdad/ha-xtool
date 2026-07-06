"""REST family coordinator — pure HTTP polling."""

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
