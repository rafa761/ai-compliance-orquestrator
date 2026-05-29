from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from orchestrator.models import OutreachTask


@dataclass(frozen=True)
class DeliveryResult:
    provider_message_id: str
    details: dict[str, object] = field(default_factory=dict)


class ChannelAdapter(Protocol):
    async def send(self, task: OutreachTask) -> DeliveryResult:
        """Send one already-claimed outreach task through a channel provider."""
        ...
