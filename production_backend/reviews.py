"""审签工单：剧本、授权、许可、泄露与翻译的再审查。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .models import ReviewStatus


class ReviewKind(str, Enum):
    SCRIPT = "剧本审签"
    CONSENT = "授权复核"
    PERMIT = "许可复核"
    SECURITY = "泄露安全审查"
    TRANSLATION = "翻译审签"


@dataclass
class ReviewTicket:
    ticket_id: str
    kind: ReviewKind
    target_id: str
    reason: str
    opened_by: str
    opened_at: datetime
    status: ReviewStatus = ReviewStatus.PENDING
    resolved_by: str = ""
    resolved_at: datetime | None = None
    resolution: str = ""


class ReviewBoard:
    """审签工单的登记与办结。"""

    def __init__(self) -> None:
        self._tickets: dict[str, ReviewTicket] = {}
        self._seq = 0

    def open(
        self, kind: ReviewKind, target_id: str, reason: str, *, actor: str
    ) -> ReviewTicket:
        self._seq += 1
        ticket = ReviewTicket(
            ticket_id=f"RV-{self._seq:04d}",
            kind=kind,
            target_id=target_id,
            reason=reason,
            opened_by=actor,
            opened_at=datetime.now(),
        )
        self._tickets[ticket.ticket_id] = ticket
        return ticket

    def resolve(
        self,
        ticket_id: str,
        *,
        actor: str,
        approved: bool = True,
        resolution: str = "",
    ) -> ReviewTicket:
        ticket = self._tickets[ticket_id]
        if ticket.status is not ReviewStatus.PENDING:
            raise ValueError(f"审签工单已办结：{ticket_id}")
        ticket.status = ReviewStatus.APPROVED if approved else ReviewStatus.REJECTED
        ticket.resolved_by = actor
        ticket.resolved_at = datetime.now()
        ticket.resolution = resolution
        return ticket

    def get(self, ticket_id: str) -> ReviewTicket:
        return self._tickets[ticket_id]

    def pending(
        self, kind: ReviewKind | None = None, target_id: str | None = None
    ) -> list[ReviewTicket]:
        return [
            t
            for t in self._tickets.values()
            if t.status is ReviewStatus.PENDING
            and (kind is None or t.kind is kind)
            and (target_id is None or t.target_id == target_id)
        ]

    def all(self) -> list[ReviewTicket]:
        return list(self._tickets.values())
