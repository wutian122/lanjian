"""
Security Control DB Models — code-audit-main Fusion (v3.1)

数据库模型: 安全控制定义、敏感操作、操作-控制关联、语言适配器缓存。
"""

import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, JSON, Integer
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.base import Base


class SecurityControlModel(Base):
    """安全控制定义 (对应 security_controls_matrix.yaml security_controls 段)"""
    __tablename__ = "security_controls"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    control_id = Column(String(50), unique=True, index=True, nullable=False)  # AUTH, AUTHZ, CSRF ...
    name = Column(String(200), nullable=False)
    name_zh = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    severity = Column(String(20), default="MEDIUM")  # CRITICAL, HIGH, MEDIUM, LOW
    cwe = Column(String(50), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class SensitiveOperationModel(Base):
    """敏感操作定义 (对应 security_controls_matrix.yaml sensitive_operations 段)"""
    __tablename__ = "sensitive_operations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(100), unique=True, index=True, nullable=False)  # delete, update, login ...
    name_zh = Column(String(200), nullable=False)
    risk_level = Column(String(20), default="MEDIUM")
    description = Column(Text, nullable=True)
    patterns = Column(JSON, default=list)  # 搜索模式列表

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # 关联: 每个操作需要哪些安全控制
    required_controls = relationship(
        "OperationRequiredControlModel",
        back_populates="operation",
        cascade="all, delete-orphan",
    )


class OperationRequiredControlModel(Base):
    """操作-控制关联表 (多对多)"""
    __tablename__ = "operation_required_controls"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    operation_id = Column(String, ForeignKey("sensitive_operations.id"), nullable=False)
    control_id = Column(String(50), nullable=False)  # 对应 SecurityControlModel.control_id

    operation = relationship("SensitiveOperationModel", back_populates="required_controls")


class LanguageAdapterModel(Base):
    """语言适配器缓存 (对应 references/adapters/*.yaml)"""
    __tablename__ = "language_adapters"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    language = Column(String(50), unique=True, index=True, nullable=False)  # python, java, go ...
    adapter_data = Column(JSON, nullable=False)  # 完整的 YAML 内容转 JSON
    file_extensions = Column(JSON, default=list)  # [".py", ".pyx"]
    framework_count = Column(Integer, default=0)  # 支持的框架数量
    control_pattern_count = Column(Integer, default=0)  # 控制检测模式数量

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
