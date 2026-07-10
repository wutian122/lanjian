from typing import Optional, List
from pydantic import BaseModel, EmailStr, field_validator


class UserRole:
    """用户角色常量"""
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    USER = "user"


class UserBase(BaseModel):
    email: Optional[str] = None
    username: Optional[str] = None
    is_active: Optional[bool] = True
    is_superuser: bool = False
    full_name: Optional[str] = None

    # Profile fields
    department: Optional[str] = None
    phone: Optional[str] = None
    avatar_url: Optional[str] = None
    role: str = UserRole.USER
    parent_admin_id: Optional[str] = None
    github_username: Optional[str] = None
    gitlab_username: Optional[str] = None


class UserCreate(BaseModel):
    username: str
    password: str
    confirm_password: str
    full_name: str
    department: str
    phone: str
    email: Optional[EmailStr] = None
    role: str = UserRole.USER
    is_active: bool = True
    is_superuser: bool = False
    parent_admin_id: Optional[str] = None
    github_username: Optional[str] = None
    gitlab_username: Optional[str] = None
    avatar_url: Optional[str] = None

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("两次输入的密码不一致")
        return v

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        allowed = {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.USER}
        if v not in allowed:
            raise ValueError(f"角色必须是以下之一: {', '.join(allowed)}")
        return v


class UserUpdate(UserBase):
    password: Optional[str] = None
    confirm_password: Optional[str] = None

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: Optional[str], info) -> Optional[str]:
        if v is not None and "password" in info.data and v != info.data["password"]:
            raise ValueError("两次输入的密码不一致")
        return v


class UserInDBBase(UserBase):
    id: str
    created_at: Optional[object] = None  # Datetime
    updated_at: Optional[object] = None

    class Config:
        from_attributes = True


class User(UserInDBBase):
    pass


class UserListResponse(BaseModel):
    users: List[User]
    total: int
    skip: int
    limit: int






