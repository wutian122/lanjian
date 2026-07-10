import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Integer, JSON, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.base import Base


class UserRole:
    """用户角色枚举"""
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    USER = "user"


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, index=True, nullable=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, index=True)
    is_active = Column(Boolean(), default=True)
    is_superuser = Column(Boolean(), default=False)


    # Security fields
    is_first_login = Column(Boolean(), default=True)
    password_history = Column(JSON, default=list)  # 最近 5 次密码哈希列表
    failed_login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    last_password_change = Column(DateTime(timezone=True), nullable=True)

    # Profile fields
    department = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)
    role = Column(String, default=UserRole.USER, nullable=False)
    github_username = Column(String, nullable=True)
    gitlab_username = Column(String, nullable=True)

    # RBAC: 上级管理员ID（普通用户所属管理员）
    parent_admin_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    subordinate_users = relationship("User",
                                      backref="parent_admin",
                                      remote_side=[id],
                                      foreign_keys=[parent_admin_id])






