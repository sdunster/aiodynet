from collections.abc import AsyncIterator, Callable

import pytest

from aiodynet import DynetClient
from aiodynet.testing import FakeGateway

type ClientFactory = Callable[..., DynetClient]


@pytest.fixture
async def gateway() -> AsyncIterator[FakeGateway]:
    async with FakeGateway() as gateway:
        yield gateway


@pytest.fixture
async def make_client(gateway: FakeGateway) -> AsyncIterator[ClientFactory]:
    """Build clients for `gateway` with fast timings, and close them afterwards."""
    clients: list[DynetClient] = []

    def factory(**kwargs: float) -> DynetClient:
        options: dict[str, float] = {
            "send_interval": 0,
            "reconnect_delay": 0.01,
            "max_reconnect_delay": 0.05,
            "connect_timeout": 1,
            **kwargs,
        }
        client = DynetClient(gateway.host, gateway.port, **options)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.close()
