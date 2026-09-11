"""Naming the copy when a connection is duplicated.

Asked for directly: duplicating a source or destination should keep every
setting and, when the name is already taken, number it -- `(1)`, `(2)`.

The numbering is separated from the copying because it is the part with edge
cases, and none of them need a database.
"""

from __future__ import annotations

from app.services.actors import next_copy_name


def test_a_free_name_is_left_alone() -> None:
    assert next_copy_name("Kho dữ liệu", set()) == "Kho dữ liệu"


def test_a_taken_name_gets_the_first_number() -> None:
    assert next_copy_name("Kho dữ liệu", {"Kho dữ liệu"}) == "Kho dữ liệu (1)"


def test_numbering_continues_past_what_exists() -> None:
    taken = {"Kho dữ liệu", "Kho dữ liệu (1)", "Kho dữ liệu (2)"}
    assert next_copy_name("Kho dữ liệu", taken) == "Kho dữ liệu (3)"


def test_a_gap_in_the_sequence_is_filled() -> None:
    """Deleting `(1)` should let the next copy reuse it rather than counting
    forever upward from the highest."""
    taken = {"Kho dữ liệu", "Kho dữ liệu (2)"}
    assert next_copy_name("Kho dữ liệu", taken) == "Kho dữ liệu (1)"


def test_copying_a_copy_continues_the_sequence() -> None:
    """`Kho dữ liệu (1) (1)` is what a naive implementation produces, and it
    gets worse every time somebody duplicates the newest one."""
    taken = {"Kho dữ liệu", "Kho dữ liệu (1)"}
    assert next_copy_name("Kho dữ liệu (1)", taken) == "Kho dữ liệu (2)"


def test_the_comparison_ignores_case() -> None:
    """The uniqueness check in the database is case-insensitive, so returning
    a name that differs only in case would be refused by the very next step."""
    assert next_copy_name("Kho Dữ Liệu", {"kho dữ liệu"}) == "Kho Dữ Liệu (1)"


def test_surrounding_space_does_not_hide_a_clash() -> None:
    assert next_copy_name("Kho dữ liệu", {"  Kho dữ liệu  "}) == "Kho dữ liệu (1)"


def test_a_number_the_user_chose_is_not_a_copy_suffix() -> None:
    """Only a trailing parenthesised number is treated as this product's own
    suffix. A name that merely contains one keeps it."""
    assert next_copy_name("Kho (Hà Nội) 2024", set()) == "Kho (Hà Nội) 2024"


def test_a_parenthesised_word_is_not_a_copy_suffix() -> None:
    assert next_copy_name("Kho (backup)", {"Kho (backup)"}) == "Kho (backup) (1)"


def test_a_very_long_name_stays_within_the_limit() -> None:
    """`ActorCreate.name` caps at 200. Producing 203 characters here would
    fail validation after the button was already pressed."""
    long_name = "K" * 200
    result = next_copy_name(long_name, {long_name})
    assert len(result) <= 200
    assert result.endswith(" (1)")


def test_a_long_name_can_still_be_copied_repeatedly() -> None:
    long_name = "K" * 200
    taken = {long_name}
    for expected in range(1, 4):
        result = next_copy_name(long_name, taken)
        assert len(result) <= 200
        assert result.endswith(f" ({expected})")
        taken.add(result)


def test_a_name_that_is_only_a_suffix_survives_stripping() -> None:
    """Stripping `(1)` off the name `(1)` leaves nothing to call the copy, so
    the original name stands rather than an empty one being returned.

    Names are non-empty by validation, so this is the degenerate case that can
    actually reach here.
    """
    assert next_copy_name("(1)", set()) == "(1)"
    assert next_copy_name("(1)", {"(1)"}) == "(1) (1)"
