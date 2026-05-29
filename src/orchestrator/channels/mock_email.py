from orchestrator.channels.base import DeliveryResult
from orchestrator.models import OutreachTask


class MockEmailAdapter:
    async def send(self, task: OutreachTask) -> DeliveryResult:
        return DeliveryResult(
            provider_message_id=f"mock_email:{task.id}",
            details={"channel": "email", "mock": True},
        )
