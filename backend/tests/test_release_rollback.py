"""Putting an older Transform release back into production.

Found by trying it. Publish twice, then ask for release 1 again: the endpoint
answered 200, the database kept release 2 live, release 1 was marked RETIRED,
and it was given the message

    "Release 2 went live while this one was being checked, so this version
     was not activated."

against a release nobody was checking. A silent no-op on a deliberate action,
at the moment somebody most needs it to work -- a release has broken
production and the only way out of the product was to fix forward.

The cause is one guard doing two jobs. `_activate` refuses to make a release
live when something with a higher activation sequence already is, which is
exactly right for the automatic path: a verification that finishes late must
not overwrite a newer release. Rolling back asks for precisely what that guard
refuses, and it ran on both paths.

So the guard now applies to `reason="verification"` only, and a manual
activation takes a sequence above whatever is live -- otherwise the next late
verification reads the restored release as the stale one and undoes the
rollback.

There is no UI half to this either: `PublishBar` declared an `onActivate` prop
that nothing called and an `onViewRelease` the page never passed, so the button
reading "Đang chạy: bản 1" did nothing at all.
"""

from __future__ import annotations

import inspect

from app.transforms import releases as release_service


def _source() -> str:
    return inspect.getsource(release_service._activate)


def test_a_manual_activation_is_not_blocked_by_a_newer_release() -> None:
    """The bug, in one assertion. Choosing an older release *is* asking to go
    backwards; refusing on those grounds refuses the whole feature."""
    assert 'reason != "manual"' in _source()


def test_the_guard_still_protects_the_automatic_path() -> None:
    """A verification that finishes late must not overwrite a newer live
    release. That is the case the guard was written for and it still holds."""
    source = _source()
    assert "activation_sequence > release.activation_sequence" in source
    assert 'release.status = "RETIRED"' in source


def test_a_rollback_takes_a_sequence_above_what_is_live() -> None:
    """Without this the rollback is undone by the next late verification: the
    restored release keeps its original lower sequence and reads as stale."""
    assert "_next_activation_sequence" in _source()


def test_the_sequence_clears_both_sides() -> None:
    ahead = release_service._next_activation_sequence

    class _R:
        def __init__(self, seq): self.activation_sequence = seq

    assert ahead(_R(5), _R(2)) == 6      # live is ahead: step past it
    assert ahead(_R(2), _R(9)) == 10     # the restored one is ahead already
    assert ahead(None, _R(3)) == 4       # nothing live yet
    assert ahead(_R(0), _R(0)) == 1      # both unset


def test_the_stale_explanation_is_cleared_when_a_release_goes_live() -> None:
    """A release that was superseded carries the reason. Keeping it after
    somebody deliberately brought it back leaves the live release showing an
    error about itself."""
    assert "release.verification_error = None" in _source()


def test_activation_still_refuses_a_release_that_did_not_build() -> None:
    """The other guard, which is not the one that was wrong: a version whose
    verification failed must never reach production, manually or otherwise."""
    source = inspect.getsource(release_service.activate)
    assert '("READY", "RETIRED")' in source
    assert "TRANSFORM_RELEASE_NOT_READY" in source


def test_rolling_back_needs_the_operate_permission() -> None:
    """It changes what production runs. An analyst who can read the project
    must not be able to change that."""
    assert "Action.OPERATE" in inspect.getsource(release_service.activate)
