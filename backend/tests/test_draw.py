"""The draw algorithm, tested over the whole input domain with Hypothesis."""

from itertools import permutations
from random import Random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.draw import DrawImpossibleError, draw


def _valid(n: int, excl: set[tuple[int, int]], result: dict[int, int]) -> bool:
    return (
        sorted(result) == list(range(n))
        and sorted(result.values()) == list(range(n))  # a permutation: one giver per receiver
        and all(g != r and (g, r) not in excl for g, r in result.items())
    )


def _any_valid_exists(n: int, excl: set[tuple[int, int]]) -> bool:
    return any(
        all(g != r and (g, r) not in excl for g, r in enumerate(p)) for p in permutations(range(n))
    )


@st.composite
def instances(draw_: st.DrawFn, max_n: int = 7) -> tuple[int, set[tuple[int, int]]]:
    n = draw_(st.integers(min_value=2, max_value=max_n))
    pairs = [(g, r) for g in range(n) for r in range(n) if g != r]
    excl = draw_(st.sets(st.sampled_from(pairs), max_size=len(pairs)))
    return n, excl


@settings(max_examples=300, deadline=None)
@given(instances(), st.integers())
def test_draw_is_valid_or_correctly_impossible(inst: tuple[int, set], seed: int) -> None:
    n, excl = inst
    exists = _any_valid_exists(n, excl)  # brute force oracle for small n
    try:
        result = draw(list(range(n)), excl, rng=Random(seed), max_samples=5)
    except DrawImpossibleError:
        assert not exists
    else:
        assert exists
        assert _valid(n, excl, result)


@settings(max_examples=50, deadline=None)
@given(st.integers(min_value=2, max_value=60))
def test_no_exclusions_always_succeeds(n: int) -> None:
    result = draw(list(range(n)))
    assert _valid(n, set(), result)


def test_matching_fallback_handles_large_tight_instances() -> None:
    # Everyone may only draw the next person: exactly one valid draw. Rejection sampling
    # can't find it; the matching fallback must, quickly.
    n = 40
    allowed_next = {(g, (g + 1) % n) for g in range(n)}
    excl = {(g, r) for g in range(n) for r in range(n) if g != r} - allowed_next
    result = draw(list(range(n)), excl, max_samples=10)
    assert result == {g: (g + 1) % n for g in range(n)}


def test_couple_cannot_draw_each_other() -> None:
    excl = {(0, 1), (1, 0)}
    for _ in range(200):
        result = draw([0, 1, 2, 3], excl)
        assert result[0] != 1 and result[1] != 0


@pytest.mark.parametrize("people", [[], [1]])
def test_too_few_people(people: list[int]) -> None:
    with pytest.raises(DrawImpossibleError):
        draw(people)


def test_two_people_swap() -> None:
    assert draw(["a", "b"]) == {"a": "b", "b": "a"}


def test_duplicates_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        draw([1, 1, 2])
