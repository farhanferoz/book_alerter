"""NotificationDelivery polymorphism: exactly one of `alert_id` /
`product_alert_id` is set per row, enforced by a CHECK constraint added in
migration 0015."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from book_alerter.db import models
from book_alerter.enums import (
    AlertKind,
    Condition,
    NotificationDeliveryStatus,
)


def _seed_book_alert(session: Session, *, isbn13: str) -> models.Alert:
    now = datetime.now(UTC)
    book = models.Book(isbn13=isbn13, title="t", author="a", created_at=now, updated_at=now)
    session.add(book)
    session.commit()
    session.refresh(book)
    assert book.id is not None
    alert = models.Alert(
        book_id=book.id,
        kind=AlertKind.NEW_LOW,
        price_minor=999,
        currency="GBP",
        source="amazon_uk",
        condition=Condition.NEW,
        message="test",
        fired_at=now,
    )
    session.add(alert)
    session.commit()
    session.refresh(alert)
    return alert


def _seed_product_alert(session: Session, *, asin: str) -> models.ProductAlert:
    now = datetime.now(UTC)
    product = models.Product(asin=asin, title="t", created_at=now, updated_at=now)
    session.add(product)
    session.commit()
    session.refresh(product)
    assert product.id is not None
    alert = models.ProductAlert(
        product_id=product.id,
        kind=AlertKind.NEW_LOW,
        price_minor=999,
        currency="GBP",
        source="amazon_uk_product",
        condition=Condition.NEW,
        message="test",
        fired_at=now,
    )
    session.add(alert)
    session.commit()
    session.refresh(alert)
    return alert


def test_alert_id_only_accepted(sqlite_engine) -> None:
    with Session(sqlite_engine) as session:
        alert = _seed_book_alert(session, isbn13="9780000000001")
        delivery = models.NotificationDelivery(
            alert_id=alert.id,
            channel="inapp",
            sent_at=datetime.now(UTC),
            status=NotificationDeliveryStatus.SENT,
        )
        session.add(delivery)
        session.commit()
        session.refresh(delivery)
        assert delivery.alert_id == alert.id
        assert delivery.product_alert_id is None


def test_product_alert_id_only_accepted(sqlite_engine) -> None:
    with Session(sqlite_engine) as session:
        alert = _seed_product_alert(session, asin="B000000002")
        delivery = models.NotificationDelivery(
            product_alert_id=alert.id,
            channel="inapp",
            sent_at=datetime.now(UTC),
            status=NotificationDeliveryStatus.SENT,
        )
        session.add(delivery)
        session.commit()
        session.refresh(delivery)
        assert delivery.alert_id is None
        assert delivery.product_alert_id == alert.id


def test_both_set_rejected(sqlite_engine) -> None:
    """The CHECK constraint must reject rows that set both FKs."""
    with Session(sqlite_engine) as session:
        book_alert = _seed_book_alert(session, isbn13="9780000000003")
        product_alert = _seed_product_alert(session, asin="B000000004")
        delivery = models.NotificationDelivery(
            alert_id=book_alert.id,
            product_alert_id=product_alert.id,
            channel="inapp",
            sent_at=datetime.now(UTC),
            status=NotificationDeliveryStatus.SENT,
        )
        session.add(delivery)
        with pytest.raises(IntegrityError, match="ck_notificationdelivery_alert_xor_product"):
            session.commit()


def test_neither_set_rejected(sqlite_engine) -> None:
    """The CHECK constraint must reject rows that set neither FK."""
    with Session(sqlite_engine) as session:
        delivery = models.NotificationDelivery(
            alert_id=None,
            product_alert_id=None,
            channel="inapp",
            sent_at=datetime.now(UTC),
            status=NotificationDeliveryStatus.SENT,
        )
        session.add(delivery)
        with pytest.raises(IntegrityError, match="ck_notificationdelivery_alert_xor_product"):
            session.commit()


def test_cascade_delete_from_product_alert(sqlite_engine) -> None:
    """Deleting a ProductAlert cascades to its NotificationDelivery rows."""
    # `SQLModel.metadata.create_all` doesn't issue the CHECK constraint and
    # also doesn't turn on the foreign_keys pragma, so this test verifies
    # both: we enable the pragma here, then prove the cascade fires.
    with sqlite_engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        with Session(bind=conn) as session:
            alert = _seed_product_alert(session, asin="B000000005")
            delivery = models.NotificationDelivery(
                product_alert_id=alert.id,
                channel="inapp",
                sent_at=datetime.now(UTC),
                status=NotificationDeliveryStatus.SENT,
            )
            session.add(delivery)
            session.commit()
            session.refresh(delivery)
            delivery_id = delivery.id

            session.delete(alert)
            session.commit()

            survivor = session.get(models.NotificationDelivery, delivery_id)
            assert survivor is None, "CASCADE didn't fire on product_alert delete"
