"""The draw algorithm: a pure function, no I/O, so it can be property-tested exhaustively.

Produces a derangement (nobody draws themselves) that respects directed exclusions.
Strategy:
  1. Rejection sampling of uniformly random permutations. Uniform over all valid draws, and
     fast for realistic groups (a random permutation is a derangement ~37% of the time).
  2. If that keeps failing (many exclusions), a randomised bipartite perfect matching. It finds
     a valid draw if one exists, so it also proves "impossible" when it returns nothing.
Randomness always comes from the OS CSPRNG (secrets.SystemRandom); never a seeded `random`.
"""

import secrets
from collections.abc import Hashable, Iterable, Sequence
from random import Random

_sysrand = secrets.SystemRandom()


class DrawImpossibleError(Exception):
    """No assignment satisfies the exclusions (or fewer than 2 participants)."""


def _allowed[T: Hashable](
    people: Sequence[T], exclusions: Iterable[tuple[T, T]]
) -> dict[T, set[T]]:
    banned = set(exclusions)
    return {g: {r for r in people if r != g and (g, r) not in banned} for g in people}


def _random_matching[T: Hashable](
    givers: list[T], allowed: dict[T, set[T]], rng: Random
) -> dict[T, T] | None:
    """Kuhn's augmenting-path bipartite matching with shuffled order: O(V*E), so it decides
    feasibility in polynomial time instead of exploring permutations."""
    owner: dict[T, T] = {}  # receiver -> giver

    def augment(giver: T, seen: set[T]) -> bool:
        options = list(allowed[giver])
        rng.shuffle(options)
        for receiver in options:
            if receiver in seen:
                continue
            seen.add(receiver)
            if receiver not in owner or augment(owner[receiver], seen):
                owner[receiver] = giver
                return True
        return False

    order = givers[:]
    rng.shuffle(order)
    for giver in order:
        if not augment(giver, set()):
            return None
    return {g: r for r, g in owner.items()}


def draw[T: Hashable](
    people: Sequence[T],
    exclusions: Iterable[tuple[T, T]] = (),
    *,
    rng: Random = _sysrand,
    max_samples: int = 2000,
) -> dict[T, T]:
    """Return {giver: receiver}. Raises DrawImpossibleError if no valid draw exists."""
    if len(set(people)) != len(people):
        raise ValueError("duplicate participants")
    if len(people) < 2:
        raise DrawImpossibleError("need at least 2 participants")

    allowed = _allowed(people, exclusions)
    if any(not options for options in allowed.values()):
        raise DrawImpossibleError("someone has nobody they are allowed to draw")

    givers = list(people)
    for _ in range(max_samples):
        receivers = givers[:]
        rng.shuffle(receivers)
        if all(r in allowed[g] for g, r in zip(givers, receivers, strict=True)):
            return dict(zip(givers, receivers, strict=True))

    result = _random_matching(givers, allowed, rng)
    if result is None:
        raise DrawImpossibleError("exclusions make a valid draw impossible")
    return result
