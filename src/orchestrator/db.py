from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from orchestrator.settings import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession without owning the request transaction boundary.

    Domain workflows decide when to commit or roll back so multi-step event
    ingestion can persist snapshots, audit rows, and planner side effects
    atomically.
    """

    async with async_session() as session:
        yield session
