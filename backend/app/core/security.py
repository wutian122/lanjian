import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Union
from jose import jwt
from passlib.context import CryptContext
from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = settings.ALGORITHM


# ============ 密码策略 ============

def validate_password_policy(password: str) -> tuple[bool, str]:
    """
    密码策略：≥12位，含大/小写字母+数字+特殊字符
    """
    if len(password) < 12:
        return False, "密码长度必须不少于 12 位"
    if not re.search(r'[A-Z]', password):
        return False, "密码必须包含大写字母"
    if not re.search(r'[a-z]', password):
        return False, "密码必须包含小写字母"
    if not re.search(r'\d', password):
        return False, "密码必须包含数字"
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "密码必须包含特殊字符"
    return True, "密码符合规范"


def check_password_history(user, new_password: str) -> bool:
    """检查新密码不在最近 5 次历史中"""
    for old_hash in (user.password_history or [])[-5:]:
        if verify_password(new_password, old_hash):
            return False
    return True


# ============ Token ============

def create_access_token(
    subject: Union[str, Any], expires_delta: timedelta = None
) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    # #3-B: access 也携带 jti —— 使登出可通过 Redis 黑名单对单个 access 即时撤销
    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "type": "access",
        "jti": str(uuid.uuid4()),
    }
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=7)
    payload = {
        "exp": expire,
        "sub": str(user_id),
        "type": "refresh",
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)
