from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            # 🔥 显式 rollback 结束未提交的事务，避免只读端点遗留 idle in transaction 连接
            # 对已 commit 的事务是 no-op，安全
            await session.rollback()
            await session.close()


@asynccontextmanager
async def async_session_factory():
    """Async context manager for creating database sessions"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()






