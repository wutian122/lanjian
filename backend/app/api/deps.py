from typing import Generator, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.core import security
from app.core.config import settings
from app.core.redis import is_blacklisted
from app.db.session import get_db
from app.models.user import User
from app.schemas import token as token_schema

reusable_oauth2 = OAuth2PasswordBearer(
    # P2-7: rstrip('/') 防止 API_V1_STR 意外带尾斜杠时 tokenUrl 出现 '//'
    tokenUrl=f"{settings.API_V1_STR.rstrip('/')}/auth/login"
)

async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token: str = Depends(reusable_oauth2)
) -> User:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        # #3-B: 登出拉黑检查 —— access 携带 jti 后，登出可即时撤销该 token
        if payload.get("jti") and await is_blacklisted(payload["jti"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="凭据已失效",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token_data = token_schema.TokenPayload(**payload)
        # 安全修复 (#3-A): 拒绝 refresh token 作为 Bearer 访问受保护端点。
        # access 有效期 30 分钟，refresh 有效期 7 天 —— 若后者可直接当 Bearer 用，
        # 攻击者持泄露的 refresh 可获得 7 天全量会话，且登出黑名单无法拦截。
        if token_data.type == "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的凭据类型",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except (JWTError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无法验证凭据",
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
