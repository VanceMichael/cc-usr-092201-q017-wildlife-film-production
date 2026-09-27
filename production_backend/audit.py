"""操作留痕：发布负责人据此核对完整记录。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AuditEntry:
    at: datetime
    actor: str
    action: str
    target: str
    detail: str


class AuditLog:
    """只增不改的留痕序列。"""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def record(self, actor: str, action: str, target: str, detail: str = "") -> None:
        self._entries.append(AuditEntry(datetime.now(), actor, action, target, detail))

    def entries(self, *, target: str | None = None) -> list[AuditEntry]:
        if target is None:
            return list(self._entries)
        return [e for e in self._entries if e.target == target]
