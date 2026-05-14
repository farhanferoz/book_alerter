from __future__ import annotations

import asyncio
import json
from typing import Any

from book_alerter.db.models import Book
from book_alerter.sources.base import (
    ObservationCandidate, Source, SourceError,
)


class SubprocessSource(Source):
    """Wraps a printing-press CLI. Subclasses provide build_command + parse."""

    def __init__(
        self,
        name: str,
        binary: str,
        region: str = "UK",
        timeout_s: int = 60,
        env: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.binary = binary
        self.region = region
        self.timeout_s = timeout_s
        self.env = env

    def build_command(self, book: Book) -> list[str]:
        # Default contract — subclasses override if their CLI uses different flags.
        return [self.binary, "search", "--isbn", book.isbn13, "--region", self.region, "--json"]

    def parse(self, stdout: str) -> list[ObservationCandidate]:
        data: dict[str, Any] = json.loads(stdout)
        offers = data.get("offers", [])
        return [ObservationCandidate(**o) for o in offers]

    async def fetch(self, book: Book) -> list[ObservationCandidate]:
        cmd = self.build_command(book)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_s
            )
        except FileNotFoundError as e:
            raise SourceError(self.name, f"binary not found: {self.binary}") from e
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise SourceError(self.name, f"timeout after {self.timeout_s}s")

        if proc.returncode != 0:
            raise SourceError(self.name, stderr.decode("utf-8", errors="replace").strip())
        return self.parse(stdout.decode("utf-8", errors="replace"))
