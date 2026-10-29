"""Deterministic offline backend. Never contacts Signal or another service."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class DemoEvent:
    kind: str
    target: str
    sender: str
    text: str
    timestamp: int
    direction: str = "incoming"
    name: str = "Temple Echo"


class DemoBackend:
    mode = "DEMO"
    description = "Offline demo. No Signal traffic."

    def __init__(self) -> None:
        self.events: deque[DemoEvent] = deque(maxlen=128)
        self.sent_count = 0

    async def start(self) -> None:
        self.events.append(DemoEvent("notice", "", "", "Temple Signal demo ready.",
                                     1_700_000_000_000, "notice", "Bridge"))

    async def close(self) -> None:
        self.events.clear()

    async def list_contacts(self) -> list[dict[str, str]]:
        return [{"id": "+12025550123", "name": "Temple Echo (offline)", "kind": "direct"}]

    async def list_groups(self) -> list[dict[str, str]]:
        return [{"id": "dGVtcGxlLWRlbW8=", "name": "16 Colors Club (offline)", "kind": "group"}]

    async def send(self, kind: str, target: str, text: str) -> int:
        self.sent_count += 1
        timestamp = int(time.time() * 1000)
        self.events.append(DemoEvent(kind, target, target if kind != "self" else "self",
                                     text, timestamp, "incoming", "Temple Echo"))
        return timestamp

    def poll(self) -> DemoEvent | None:
        return self.events.popleft() if self.events else None
