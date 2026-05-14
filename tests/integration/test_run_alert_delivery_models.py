from datetime import UTC, datetime

from sqlmodel import Session, select

from book_alerter.db import models


def test_source_run_round_trip(sqlite_engine):
    with Session(sqlite_engine) as s:
        run = models.SourceRun(
            source="bookfinder",
            started_at=datetime.now(UTC),
            status="running",
        )
        s.add(run); s.commit(); s.refresh(run)
        loaded = s.exec(select(models.SourceRun)).one()
        assert loaded.id == run.id
        assert loaded.status == "running"
        assert loaded.books_attempted == 0


def test_alert_round_trip(sqlite_engine, make_book):
    with Session(sqlite_engine) as s:
        book = make_book(s)
        alert = models.Alert(
            book_id=book.id,
            kind="new_low",
            price_minor=900,
            currency="GBP",
            source="bookfinder",
            condition="new",
            message="all-time low",
            fired_at=datetime.now(UTC),
            delivered_via=["pushover"],
        )
        s.add(alert); s.commit(); s.refresh(alert)
        loaded = s.exec(select(models.Alert)).one()
        assert loaded.book_id == book.id
        assert loaded.kind == "new_low"
        assert loaded.delivered_via == ["pushover"]


def test_notification_delivery_round_trip(sqlite_engine, make_book):
    with Session(sqlite_engine) as s:
        book = make_book(s)
        alert = models.Alert(
            book_id=book.id, kind="target_hit", price_minor=500, currency="GBP",
            source="bookfinder", condition="new", message="hit",
            fired_at=datetime.now(UTC),
        )
        s.add(alert); s.commit(); s.refresh(alert)

        delivery = models.NotificationDelivery(
            alert_id=alert.id,
            channel="pushover",
            sent_at=datetime.now(UTC),
            status="sent",
        )
        s.add(delivery); s.commit(); s.refresh(delivery)
        loaded = s.exec(select(models.NotificationDelivery)).one()
        assert loaded.alert_id == alert.id
        assert loaded.status == "sent"


def test_book_signal_state_round_trip(sqlite_engine, make_book):
    with Session(sqlite_engine) as s:
        book = make_book(s)
        state = models.BookSignalState(
            book_id=book.id,
            last_signal="new_low",
            last_all_time_min_total_minor=900,
            last_evaluated_at=datetime.now(UTC),
        )
        s.add(state); s.commit()
        loaded = s.exec(select(models.BookSignalState)).one()
        assert loaded.book_id == book.id
        assert loaded.last_signal == "new_low"
        assert loaded.last_all_time_min_total_minor == 900
