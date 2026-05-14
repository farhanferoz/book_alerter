from __future__ import annotations

from datetime import UTC, datetime

import pytest

from book_alerter.db import models


@pytest.fixture
def transient_book():
    def _make(isbn: str = "9780000000000") -> models.Book:
        now = datetime.now(UTC)
        return models.Book(
            isbn13=isbn, title="t", author="a", created_at=now, updated_at=now,
        )

    return _make
