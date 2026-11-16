"""The invariants the database itself guarantees, whatever the application code does."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import Animation, Assignment, Event, Participant, RateLimitHit, Session
from app.db.session import SessionLocal
from tests.conftest import Seeded, code_hash


async def _count(model: type) -> int:
    async with SessionLocal() as s:
        return await s.scalar(select(func.count()).select_from(model)) or 0


async def test_assignment_unique_giver_and_receiver(seeded: Seeded) -> None:
    a, b, c = (seeded.participant_ids[n] for n in ("Person A", "Person B", "Person C"))
    async with SessionLocal() as s:
        s.add(Assignment(event_id=seeded.event_id, giver_id=a, receiver_id=b))
        await s.commit()
    for giver, receiver in ((a, c), (c, b)):  # second receiver for A; second giver for B
        async with SessionLocal() as s:
            s.add(Assignment(event_id=seeded.event_id, giver_id=giver, receiver_id=receiver))
            with pytest.raises(IntegrityError):
                await s.commit()


async def test_assignment_rejects_self_draw(seeded: Seeded) -> None:
    a = seeded.participant_ids["Person A"]
    async with SessionLocal() as s:
        s.add(Assignment(event_id=seeded.event_id, giver_id=a, receiver_id=a))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_code_hash_is_unique(seeded: Seeded) -> None:
    async with SessionLocal() as s:
        s.add(
            Participant(
                event_id=seeded.event_id,
                display_name="Person Z",
                vocative_name="Person Z",
                accusative_name="Person Z",
                code_hash=code_hash(seeded.codes["Person A"]),
            )
        )
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_partner_is_set_null_when_partner_is_deleted(seeded: Seeded) -> None:
    a, b = seeded.participant_ids["Person A"], seeded.participant_ids["Person B"]
    async with SessionLocal() as s:
        pa, pb = await s.get(Participant, a), await s.get(Participant, b)
        assert pa is not None
        assert pb is not None
        pa.partner_id, pb.partner_id = b, a
        await s.commit()
    async with SessionLocal() as s:
        pb = await s.get(Participant, b)
        assert pb is not None
        await s.delete(pb)
        await s.commit()
    async with SessionLocal() as s:
        pa = await s.get(Participant, a)
        assert pa is not None  # the partner survives...
        assert pa.partner_id is None  # ...with the link cleared


async def test_participant_cannot_be_own_partner(seeded: Seeded) -> None:
    async with SessionLocal() as s:
        pa = await s.get(Participant, seeded.participant_ids["Person A"])
        assert pa is not None
        pa.partner_id = pa.id
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_one_animation_per_participant(seeded: Seeded) -> None:
    a = seeded.participant_ids["Person A"]
    clip = Animation(participant_id=a, video=b"fake-mp4", content_type="video/mp4", byte_size=8)
    async with SessionLocal() as s:
        s.add(clip)
        await s.commit()
    async with SessionLocal() as s:
        s.add(Animation(participant_id=a, video=b"x", content_type="video/mp4", byte_size=1))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_deleting_the_event_removes_everything_under_it(seeded: Seeded) -> None:
    a, b = seeded.participant_ids["Person A"], seeded.participant_ids["Person B"]
    async with SessionLocal() as s:
        s.add_all(
            [
                Assignment(event_id=seeded.event_id, giver_id=a, receiver_id=b),
                Animation(participant_id=b, video=b"x", content_type="video/mp4", byte_size=1),
                Session(participant_id=a, token_hash="0" * 64),
                RateLimitHit(key_hash="1" * 64),
            ]
        )
        await s.commit()
    async with SessionLocal() as s:
        event = await s.get(Event, seeded.event_id)
        assert event is not None
        await s.delete(event)
        await s.commit()
    for model in (Participant, Assignment, Animation, Session):
        assert await _count(model) == 0, model.__name__
    # Rate-limit hits belong to no event, so they are not part of the cascade.
    assert await _count(RateLimitHit) == 1
