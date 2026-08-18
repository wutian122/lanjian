from typing import Optional
from pydantic import BaseModel

class Token(BaseModel):
    access_token: str
    token_type: str
    refresh_token: Optional[str] = None
    is_first_login: Optional[bool] = None
    # 用户信息（登录时返回）
    user_id: Optional[str] = None
    username: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None

class TokenPayload(BaseModel):
    sub: Optional[str] = None
    type: Optional[str] = None
    jti: Optional[str] = None






