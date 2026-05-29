from orchestrator.channels.base import DeliveryResult
from orchestrator.models import OutreachTask


class MockSmsAdapter:
    async def send(self, task: OutreachTask) -> DeliveryResult:
        return DeliveryResult(
            provider_message_id=f"mock_sms:{task.id}",
            details={"channel": "sms", "mock": True},
        )
