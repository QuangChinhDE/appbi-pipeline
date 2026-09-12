"""Putting a Transform project on a schedule.

Reported as "there is nowhere to set one". There was not, and the reason was
not that the feature was missing -- all of it existed and none of it was
reachable:

    models.py       schedule_type, schedule_config, schedule_command columns
    projects.py     validates the command, computes next_run_at
    worker.py       transform_scheduler_loop, polling for it all along
    api.py          PATCH already accepted all three fields

Two things stood between that and a usable feature.

The endpoint returned 500. `present()` reads `project.updated_at`, which
carries `onupdate=func.now()` -- a value the database computes, so SQLAlchemy
expires the attribute after an UPDATE. Reading it from synchronous code inside
an async request is a lazy SELECT, and that is `MissingGreenlet`. Measured:
PATCH /transforms/{id} answered INTERNAL_ERROR while the schedule it was
setting had already been written. The same class of fault as the pipeline
retry endpoint, in a different module.

And what came back described none of it. `ProjectView` carried `schedule_type`
and `next_run_at` and not the interval, the time of day, the cron expression
or the dbt command, so nothing could have drawn an editor from the response.

Proven end to end afterwards on a Postgres warehouse: schedule set to every
five minutes, and the worker fired it --

    05:48:07  SCHEDULE  SUCCEEDED   next_run_at moved to 05:53:07
"""

from __future__ import annotations

import inspect

import pytest

from app.transforms import projects as project_service
from app.transforms.runtime.commands import SCHEDULABLE
from app.transforms.schemas import ProjectUpdate, ProjectView


# ── what the API reports back ────────────────────────────────────────────

@pytest.mark.parametrize("field", [
    "schedule_type", "schedule_config", "schedule_command", "timezone", "next_run_at",
])
def test_the_whole_schedule_is_reported(field: str) -> None:
    """An editor has to be populated from something. Reporting the type alone
    let the UI say a schedule existed without being able to say what it was."""
    assert field in ProjectView.model_fields


@pytest.mark.parametrize("field", [
    "schedule_type", "schedule_config", "schedule_command", "timezone",
])
def test_what_is_reported_can_also_be_set(field: str) -> None:
    """Read and write have to cover the same ground, or the form loads a value
    it cannot save."""
    assert field in ProjectUpdate.model_fields


def test_the_presenter_includes_them() -> None:
    source = inspect.getsource(project_service.present)
    for key in ("schedule_config", "schedule_command", "timezone"):
        assert f'"{key}"' in source


# ── the 500 ──────────────────────────────────────────────────────────────

def test_detail_refreshes_before_presenting() -> None:
    """`updated_at` has `onupdate=func.now()`, so after any UPDATE its value
    lives only in the database and the attribute is expired. A synchronous
    presenter reading it is a lazy SELECT inside an async request, which is
    the MissingGreenlet every mutating caller hit."""
    source = inspect.getsource(project_service.detail)
    assert "await session.refresh(project)" in source
    assert source.index("await session.refresh(project)") < source.index("present(")


# ── what a schedule may run ──────────────────────────────────────────────

def test_the_ui_offers_exactly_what_the_backend_accepts() -> None:
    """The dialog lists these six. A seventh in either place is a picker that
    produces TRANSFORM_COMMAND_NOT_SCHEDULABLE, or a command nobody can
    choose."""
    assert SCHEDULABLE == {"build", "run", "test", "seed", "snapshot", "source-freshness"}


@pytest.mark.parametrize("command", ["compile", "docs-generate", "debug", "deps", "ls"])
def test_commands_that_produce_nothing_are_refused(command: str) -> None:
    """A schedule exists to change the warehouse. `dbt compile` at 03:00 every
    night would burn a container and leave no trace of why."""
    assert command not in SCHEDULABLE


def test_the_scheduler_loop_is_still_wired() -> None:
    """The half that was never broken, pinned so it stays that way: the worker
    polls for projects whose next_run_at has passed."""
    from app import worker
    source = inspect.getsource(worker)
    assert "transform_scheduler_loop" in source
    assert "TransformProject.next_run_at" in source
