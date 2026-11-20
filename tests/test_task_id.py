"""Tests for the Task ID sensor state (issue #4).

The laser reports no task ID between jobs. The sensor shows "None" when
the laser is known to have no job, and Unknown only when that is not
known.
"""

from __future__ import annotations

import pytest

from .features import requires

pytestmark = requires("task_id_none")


def _value(status, task_id):
    from custom_components.xtool.const import task_id_value

    return task_id_value(status, task_id)


@pytest.mark.parametrize("name", ["IDLE", "SLEEPING"])
def test_no_job_shows_none(name):
    from custom_components.xtool.const import XtoolStatus

    assert _value(XtoolStatus[name], "") == "None"


def test_idle_hides_the_last_jobs_id():
    from custom_components.xtool.const import XtoolStatus

    assert _value(XtoolStatus.IDLE, "abc") == "None"


@pytest.mark.parametrize("name", ["PROCESSING", "PAUSED", "FRAMING", "FINISHED"])
def test_known_task_shows_its_id(name):
    from custom_components.xtool.const import XtoolStatus

    assert _value(XtoolStatus[name], "abc") == "abc"


def test_finished_without_an_id_shows_none():
    from custom_components.xtool.const import XtoolStatus

    assert _value(XtoolStatus.FINISHED, "") == "None"


@pytest.mark.parametrize("name", ["PROCESSING", "PAUSED", "UNKNOWN", "INITIALIZING"])
def test_job_without_an_id_is_unknown(name):
    from custom_components.xtool.const import XtoolStatus

    assert _value(XtoolStatus[name], "") is None


def test_no_status_is_unknown():
    assert _value(None, "") is None


@pytest.mark.parametrize(
    "module", ["ws_v2", "rest", "s1"],
)
def test_every_task_id_sensor_uses_the_rule(module):
    import importlib

    from custom_components.xtool.const import XtoolStatus

    entities = importlib.import_module(
        f"custom_components.xtool.protocols.{module}.entities"
    )
    descriptions = [
        d for name in dir(entities)
        for v in [getattr(entities, name)]
        if isinstance(v, (list, tuple))
        for d in v
        if getattr(d, "key", None) == "task_id"
    ]
    assert descriptions, module

    class State:
        status = XtoolStatus.IDLE
        task_id = ""

    for d in descriptions:
        assert d.value_fn(State(), None) == "None"
