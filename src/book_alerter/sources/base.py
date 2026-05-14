from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from book_alerter.db.models import Book, Condition

__all__ = ["Condition", "ObservationCandidate", "Source", "SourceError"]


class ObservationCandidate(BaseModel):
    seller: str | None = None
    condition: Condition
    price_minor: int
    shipping_minor: int | None = None
    currency: str
    url: str


class SourceError(Exception):
    def __init__(self, source_name: str, message: str) -> None:
        super().__init__(f"[{source_name}] {message}")
        self.source_name = source_name
        self.message = message


class Source(ABC):
    name: str

    @abstractmethod
    async def fetch(self, book: Book) -> list[ObservationCandidate]: ...

    async def healthcheck(self) -> bool:
        return True
