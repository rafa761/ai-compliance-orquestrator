from orchestrator.channels.base import DeliveryResult
from orchestrator.models import OutreachTask


class MockCallAdapter:
    async def send(self, task: OutreachTask) -> DeliveryResult:
        return DeliveryResult(
            provider_message_id=f"mock_call:{task.id}",
            details={"channel": "call", "mock": True},
        )
