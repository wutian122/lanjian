import io
import logging
import random
import string
import time
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from pydantic import BaseModel, EmailStr, field_validator

from app.api import deps
from app.core import security
from app.core.config import settings
from app.core.rbac import UserRole
from app.core.redis import get_redis, is_blacklisted
from app.db.session import get_db
from app.models.user import User
from app.models.audit_log import AuditLog
from app.schemas.token import Token
from app.schemas.user import User as UserSchema, UserCreate

router = APIRouter()

logger = logging.getLogger(__name__)

# ============ 安全常量 ============
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15
CAPTCHA_TTL_SECONDS = 300  # 验证码 5 分钟过期
# P3-5: 内存 captcha 存储上限。旧实现无上限，未认证用户可无限刷 /captcha 拖爆内存。
# 命中上限时先做一次全量过期清理，仍满则拒绝新申请。
CAPTCHA_MAX_ENTRIES = 10_000

# ============ 验证码内存存储（生产环境建议替换为 Redis） ============
_captcha_store: dict[str, dict] = {}

class CaptchaStoreFull(Exception):
    """captcha 内存池达到上限。上层应返回 503。"""


def _store_captcha(captcha_id: str, code: str) -> None:
    # P3-5: DoS 防护 —— 命中上限先清理过期，仍满就拒绝
    if len(_captcha_store) >= CAPTCHA_MAX_ENTRIES:
        _clean_expired_captchas()
    if len(_captcha_store) >= CAPTCHA_MAX_ENTRIES:
        raise CaptchaStoreFull(
            f"captcha store is full ({CAPTCHA_MAX_ENTRIES}); "
            "possible DoS. Wait for TTL expiry or restart backend."
        )
    _captcha_store[captcha_id] = {"code": code, "expires_at": time.time() + CAPTCHA_TTL_SECONDS}

def _verify_captcha(captcha_id: str, code: str) -> bool:
    entry = _captcha_store.pop(captcha_id, None)
    if not entry:
        return False
    if time.time() > entry["expires_at"]:
        return False
    return entry["code"].upper() == code.upper()

def _clean_expired_captchas() -> None:
    now = time.time()
    expired = [k for k, v in _captcha_store.items() if now > v["expires_at"]]
    for k in expired:
        _captcha_store.pop(k, None)


class RegisterRequest(BaseModel):
    username: str
    password: str
    confirm_password: str
    full_name: str
    department: str
    phone: str
    email: Optional[str] = None

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("两次输入的密码不一致")
        return v


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


# ============ 验证码端点 ============

@router.get("/captcha")
async def get_captcha():
    """生成验证码图片，code 存入内存（5 分钟 TTL），仅返回 captcha_id"""
    captcha_id = str(uuid_lib.uuid4())
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))

    # 清理过期验证码
    _clean_expired_captchas()

    # 存入内存存储
    # P3-5: 命中上限 → 返回 503，让前端知道服务饱和（一般意味着遭到刷量攻击）
    try:
        _store_captcha(captcha_id, code)
    except CaptchaStoreFull as e:
        logger.warning(f"[captcha] store full: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="验证码服务繁忙，请稍后重试",
        )

    # 生成简单验证码图片
    try:
        from PIL import Image, ImageDraw
        img = Image.new('RGB', (120, 40), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.text((15, 8), code, fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
    except ImportError:
        # PIL 不可用时返回文本验证码（仅 captcha_id，无 code）
        return {"captcha_id": captcha_id}

    # 仅返回 captcha_id，code 不暴露给客户端
    return StreamingResponse(
        buf,
        media_type="image/png",
        headers={
            "X-Captcha-Id": captcha_id,
        }
    )


# ============ 登录端点 ============

@router.post("/login", response_model=Token)
async def login(
    db: AsyncSession = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
    captcha_code: Optional[str] = Query(None),
    captcha_id: Optional[str] = Query(None),
) -> Any:
    """
    OAuth2 compatible token login with security enhancements.
    Supports login by username or email.
    """
    # 验证码校验（可选，前端未实现时跳过）
    if captcha_id and captcha_code:
        if not _verify_captcha(captcha_id, captcha_code):
            raise HTTPException(status_code=400, detail="验证码错误或已过期")

    result = await db.execute(
        select(User).where(
            (User.username == form_data.username) | (User.email == form_data.username)
        )
    )
    user = result.scalars().first()

    # 检查账户锁定
    if user and user.locked_until and user.locked_until > datetime.now(timezone.utc):
        remaining = int((user.locked_until - datetime.now(timezone.utc)).total_seconds() / 60)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"账户已锁定，请 {remaining} 分钟后重试"
        )

    # 验证凭据
    if not user or not security.verify_password(form_data.password, user.hashed_password):
        if user:
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
                user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
            # #2: 审计 —— 登录失败留痕（仅记录到用户，不落密码相关敏感信息）
            db.add(AuditLog(
                action="login_failed",
                actor_id=user.id,
                target_type="user",
                target_id=user.id,
                details={"username": user.username},
            ))
            await db.commit()
        raise HTTPException(status_code=400, detail="用户名或密码错误")

    if not user.is_active:
        raise HTTPException(status_code=400, detail="用户已被禁用")

    # 登录成功：重置失败计数和锁定
    user.failed_login_attempts = 0
    user.locked_until = None
    # #2: 审计 —— 登录成功留痕
    db.add(AuditLog(
        action="login_success",
        actor_id=user.id,
        target_type="user",
        target_id=user.id,
        details={"username": user.username},
    ))
    await db.commit()

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return {
        "access_token": security.create_access_token(
            user.id, expires_delta=access_token_expires
        ),
        "token_type": "bearer",
        "refresh_token": security.create_refresh_token(user.id),
        "is_first_login": user.is_first_login,
        "user_id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "role": user.role,
        "department": user.department,
    }


# ============ 刷新令牌端点 ============

@router.post("/refresh", response_model=Token)
async def refresh_token(
    request: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """使用刷新令牌获取新的访问令牌"""
    from jose import jwt, JWTError

    try:
        payload = jwt.decode(
            request.refresh_token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="无效的刷新令牌")
        # #3-B: 已被登出拉黑的 refresh 不允许再续期
        if payload.get("jti") and await is_blacklisted(payload["jti"]):
            raise HTTPException(status_code=401, detail="刷新令牌已失效")
        user_id = payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="无效的刷新令牌")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return {
        "access_token": security.create_access_token(
            user.id, expires_delta=access_token_expires
        ),
        "token_type": "bearer",
        "refresh_token": security.create_refresh_token(user.id),
    }


# ============ 注册端点 ============

@router.post("/register", response_model=UserSchema)
async def register(
    *,
    db: AsyncSession = Depends(get_db),
    user_in: RegisterRequest,
) -> Any:
    """Register a new user with password policy validation."""
    # 检查是否允许公开注册
    if not settings.ALLOW_PUBLIC_REGISTRATION:
        raise HTTPException(status_code=403, detail="公开注册已关闭，请联系管理员创建账户")

    # 密码策略验证
    valid, msg = security.validate_password_policy(user_in.password)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    # Check if username already exists
    result = await db.execute(select(User).where(User.username == user_in.username))
    existing_user = result.scalars().first()
    if existing_user:
        raise HTTPException(status_code=400, detail="该用户名已被使用")

    # Check if email already exists (if provided)
    if user_in.email:
        result = await db.execute(select(User).where(User.email == user_in.email))
        existing_email = result.scalars().first()
        if existing_email:
            raise HTTPException(status_code=400, detail="该邮箱已被注册")

    # Check if this is the first user (make them superadmin)
    # P2-6: 改用 SELECT COUNT(*) LIMIT 1；旧版 select(User).scalars().all() 会拉全表。
    count_result = await db.execute(select(func.count()).select_from(User).limit(1))
    total_users = count_result.scalar() or 0
    is_first_user = total_users == 0

    # Create new user
    # 第一个注册用户为超级管理员，后续注册用户为普通用户
    db_user = User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=security.get_password_hash(user_in.password),
        full_name=user_in.full_name,
        department=user_in.department,
        phone=user_in.phone,
        is_active=True,
        is_superuser=is_first_user,
        role=UserRole.SUPER_ADMIN if is_first_user else UserRole.USER,
        is_first_login=True,
        password_history=[security.get_password_hash(user_in.password)],
        last_password_change=datetime.now(timezone.utc),
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


# ============ 修改密码端点 ============

@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """修改密码（含策略验证和历史检查）"""
    # 验证旧密码
    if not security.verify_password(request.old_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="旧密码错误")

    # 密码策略验证
    valid, msg = security.validate_password_policy(request.new_password)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    # 密码历史检查
    if not security.check_password_history(current_user, request.new_password):
        raise HTTPException(status_code=400, detail="新密码不能与最近 5 次使用的密码相同")

    # 更新密码
    current_user.hashed_password = security.get_password_hash(request.new_password)
    current_user.is_first_login = False
    current_user.last_password_change = datetime.now(timezone.utc)

    # 更新密码历史（保留最近 5 次）
    history = (current_user.password_history or [])[-4:]  # 保留旧 4 条
    history.append(current_user.hashed_password)
    current_user.password_history = history

    # #2: 审计 —— 修改密码留痕
    db.add(AuditLog(
        action="change_password",
        actor_id=current_user.id,
        target_type="user",
        target_id=current_user.id,
        details={},
    ))
    await db.commit()
    return {"message": "密码修改成功"}


# ============ 退出登录端点 ============

class LogoutRequest(BaseModel):
    # #3-B: 前端实际只持有 access_token；refresh_token 保留兼容（后端可能用它撤销续期）
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None


@router.post("/logout")
async def logout(
    request: LogoutRequest,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """退出登录：把当前 access（以及可选 refresh）的 jti 写入 Redis 黑名单，使其即时失效。

    #3-B 修复：旧实现只拉黑 refresh 的 jti 且无人查询（纯仪式）；现在：
    - access 带 jti，登出后 get_current_user 查到黑名单即 401（登出真正生效）
    - refresh 带 jti，登出后可阻止其继续 /refresh 续期
    按异常类型分级处理（P3-2 保留）：
      - JWT 解码失败：token 篡改/过期 → 仍当"已退出"响应（无需报错）
      - Redis 掉线：黑名单写不进 → 抛 503，让前端知道要重试
    """
    if not (request.access_token or request.refresh_token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="缺少要撤销的 token",
        )

    from jose import JWTError
    import jwt

    now = int(time.time())
    try:
        redis = await get_redis()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="登出服务暂时不可用，请重试",
        )

    # 逐个处理 access_token / refresh_token（都带 jti），任一拉黑成功即生效
    for token in (request.access_token, request.refresh_token):
        if not token:
            continue
        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM],
                options={"verify_exp": False},
            )
            jti = payload.get("jti")
            exp = payload.get("exp")
            if not jti:
                # 老版本 token 没有 jti，无法拉黑
                continue
            ttl = max(1, int(exp or 0) - now)
            await redis.setex(f"logout:blacklist:{jti}", ttl, "1")
        except JWTError:
            # 篡改/无法解码的 token 无法拉黑，但登出语义仍成立
            logger.debug("[logout] token 解码失败（已忽略）")
            continue

    return {"message": "已退出登录"}
