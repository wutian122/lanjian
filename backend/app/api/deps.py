import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core import security
from app.core.config import settings
from app.core.redis import get_redis
from app.db.session import get_db
from app.models.user import User
from app.schemas import token as token_schema

logger = logging.getLogger(__name__)

reusable_oauth2 = OAuth2PasswordBearer(
    # P2-7: rstrip('/') 防止 API_V1_STR 意外带尾斜杠时 tokenUrl 出现 '//'
    tokenUrl=f"{settings.API_V1_STR.rstrip('/')}/auth/login"
)


def _logout_blacklist_key(jti: str) -> str:
    return "logout:blacklist:" + str(jti)


async def _is_token_blacklisted(jti: str | None) -> bool:
    """令牌是否在登出黑名单中。Redis 不可用时 fail-open（仅登出强失效降级，不阻断认证）。"""
    if not jti:
        return False
    try:
        redis = await get_redis()
        return bool(await redis.get(_logout_blacklist_key(jti)))
    except Exception:
        logger.warning("[auth] token blacklist check skipped (redis unavailable)")
        return False


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token: str = Depends(reusable_oauth2)
) -> User:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        token_data = token_schema.TokenPayload(**payload)
    except (JWTError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无法验证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # A2: 只接受 access 令牌——refresh 令牌（7 天有效）不得当访问令牌使用
    if token_data.type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌类型无效",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # A2: 登出黑名单校验（带 jti 的令牌被登出后立即失效）
    if await _is_token_blacklisted(token_data.jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.id == token_data.sub))
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="用户已被禁用")
    return user

async def get_current_active_superuser(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=400, detail="权限不足"
        )
    return current_user
