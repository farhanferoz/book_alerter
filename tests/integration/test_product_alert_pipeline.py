"""Integration tests for the product-side AlertPipeline.

Mirrors the book-side test_alert_pipeline.py shape but instantiates the
pipeline with `models=PRODUCT_MODELS` and seeds `ProductObservation` rows.
Asserts: ProductAlert rows persist, ProductSignalState writes happen,
NotificationDelivery uses `product_alert_id` (not `alert_id`), dedup +
mute + quiet hours work identically to the book path.

Together with the books-side tests, these prove the schema parameterisation
introduced in P2b doesn't skew kind-specific behaviour.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from book_alerter.config import Config, NotificationsConfig, RecommendationConfig
from book_alerter.db import models
from book_alerter.enums import AlertKind, Condition, NotificationDeliveryStatus
from book_alerter.notifications.base import AlertLike, ItemLike, Notifier
from book_alerter.notifications.dispatcher import PRODUCT_MODELS, AlertPipeline
from book_alerter.notifications.inapp import InAppNotifier


class _RecordingNotifier(Notifier):
    name = "ntfy"

    def __init__(self) -> None:
        self.calls: list[tuple[int | None, int | None]] = []

    async def send(self, alert: AlertLike, item: ItemLike) -> dict:
        self.calls.append((alert.id, item.id))
        return {"status": "sent"}


def _seed_product_observations(
    session: Session,
    *,
    product_id: int,
    totals: list[int],
    source_prefix: str = "src",
) -> None:
    now = datetime.now(UTC)
    for i, total in enumerate(totals):
        when = now - timedelta(minutes=i)
        session.add(
            models.ProductObservation(
                product_id=product_id,
                source=f"{source_prefix}_{i:02d}",
                condition=Condition.NEW,
                price_minor=total,
                currency="GBP",
                shipping_minor=0,
                total_minor=total,
                url=f"https://example.com/{i}",
                observed_at=when,
                last_seen_at=when,
                raw={},
            ),
        )
    session.commit()


def _make_cfg(**notif_overrides) -> Config:
    # Quiet hours OFF unless a test asks for them. `NotificationsConfig`
    # defaults to 22:00-08:00 Europe/London, and the dispatcher skips every
    # notifier that does not set `bypasses_quiet_hours` inside that window --
    # so a test asserting `_RecordingNotifier` was called passed by day and
    # failed by night. Reproduced: the FK-routing test fails at 22:30 and
    # passes with the clock frozen at 12:00, same code. The scenarios already
    # disable quiet hours for this reason (scenario_01, _04, _06, _07); this
    # file was the one that did not. A test that wants the window can still
    # pass `quiet_hours=QuietHours(...)` through the overrides.
    notif_overrides.setdefault("quiet_hours", None)
    return Config(
        recommendation=RecommendationConfig(
            min_days_of_history=0,
            min_observations_for_signal=14,
            alert_dedup_window_hours=24,
        ),
        notifications=NotificationsConfig(**notif_overrides),
    )


def _run(pipeline: AlertPipeline, ids: list[int]) -> None:
    asyncio.run(pipeline.run(ids))


def test_product_pipeline_writes_alert_and_delivery_on_target_hit(
    engine_with_view, make_product,
) -> None:
    cfg = _make_cfg()
    with Session(engine_with_view) as s:
        product = make_product(s, asin="B070100001")
        product.target_price_minor = 1000
        s.add(product)
        s.commit()
        s.refresh(product)
        _seed_product_observations(
            s, product_id=product.id, totals=[900 + i for i in range(14)],
        )
        product_id = product.id

    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine_with_view),
        notifiers=[InAppNotifier()],
        models=PRODUCT_MODELS,
    )
    _run(pipeline, [product_id])

    with Session(engine_with_view) as s:
        alerts = s.exec(
            select(models.ProductAlert).where(
                models.ProductAlert.product_id == product_id,
            ),
        ).all()
        assert len(alerts) >= 1
        kinds = {a.kind for a in alerts}
        assert AlertKind.TARGET_HIT in kinds

        deliveries = s.exec(
            select(models.NotificationDelivery).where(
                models.NotificationDelivery.product_alert_id == alerts[0].id,
            ),
        ).all()
        assert len(deliveries) == 1
        assert deliveries[0].channel == "inapp"
        assert deliveries[0].status == NotificationDeliveryStatus.SENT
        # The polymorphic CHECK constraint demands `alert_id` is NULL on a
        # product-side delivery row.
        assert deliveries[0].alert_id is None

        # ProductSignalState got persisted for the next eval.
        state = s.get(models.ProductSignalState, product_id)
        assert state is not None
        assert state.last_evaluated_at is not None


def test_product_pipeline_dedups_within_window(
    engine_with_view, make_product,
) -> None:
    cfg = _make_cfg()
    with Session(engine_with_view) as s:
        product = make_product(s, asin="B070100002")
        product.target_price_minor = 1000
        s.add(product)
        s.commit()
        s.refresh(product)
        _seed_product_observations(
            s, product_id=product.id, totals=[900 + i for i in range(14)],
        )
        product_id = product.id

    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine_with_view),
        notifiers=[InAppNotifier()],
        models=PRODUCT_MODELS,
    )
    # Run twice back-to-back; second run must dedup.
    _run(pipeline, [product_id])
    _run(pipeline, [product_id])

    with Session(engine_with_view) as s:
        alerts = s.exec(
            select(models.ProductAlert).where(
                models.ProductAlert.product_id == product_id,
                models.ProductAlert.kind == AlertKind.TARGET_HIT,
            ),
        ).all()
        # Exactly one target_hit row despite two pipeline runs.
        assert len(alerts) == 1


def test_product_pipeline_skips_when_muted(
    engine_with_view, make_product,
) -> None:
    cfg = _make_cfg()
    with Session(engine_with_view) as s:
        product = make_product(s, asin="B070100003")
        product.target_price_minor = 1000
        product.muted_until = datetime.now(UTC) + timedelta(hours=1)
        s.add(product)
        s.commit()
        s.refresh(product)
        _seed_product_observations(
            s, product_id=product.id, totals=[900 + i for i in range(14)],
        )
        product_id = product.id

    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine_with_view),
        notifiers=[InAppNotifier()],
        models=PRODUCT_MODELS,
    )
    _run(pipeline, [product_id])

    with Session(engine_with_view) as s:
        alerts = s.exec(
            select(models.ProductAlert).where(
                models.ProductAlert.product_id == product_id,
            ),
        ).all()
        assert len(alerts) == 0


def test_product_pipeline_routes_to_correct_notifier_delivery_fk(
    engine_with_view, make_product,
) -> None:
    """Regression test for the polymorphic NotificationDelivery FK routing.

    The dispatcher writes the delivery row with the kind-specific FK column
    (alert_id for books, product_alert_id for products). The CHECK constraint
    rejects rows that set both or neither, so an off-by-one here would fail
    the commit loudly — but we still pin it explicitly."""
    cfg = _make_cfg()
    with Session(engine_with_view) as s:
        product = make_product(s, asin="B070100004")
        product.target_price_minor = 1000
        s.add(product)
        s.commit()
        s.refresh(product)
        _seed_product_observations(
            s, product_id=product.id, totals=[900 + i for i in range(14)],
        )
        product_id = product.id

    rec = _RecordingNotifier()
    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=lambda: Session(engine_with_view),
        notifiers=[InAppNotifier(), rec],
        models=PRODUCT_MODELS,
    )
    _run(pipeline, [product_id])

    # Recorder received the call with the product id.
    assert len(rec.calls) == 1
    _alert_id, item_id = rec.calls[0]
    assert item_id == product_id

    with Session(engine_with_view) as s:
        deliveries = s.exec(
            select(models.NotificationDelivery).where(
                models.NotificationDelivery.product_alert_id.is_not(None),  # type: ignore[union-attr]
            ),
        ).all()
        # In-app + ntfy stubs each delivered once.
        assert len(deliveries) == 2
        # All have product_alert_id set and alert_id null (CHECK constraint
        # would have rejected anything else).
        for d in deliveries:
            assert d.product_alert_id is not None
            assert d.alert_id is None
