"""
Coverage Tracking DB Model — code-audit-main Fusion (v3.1)

D1-D10 维度覆盖追踪, 按审计任务记录各安全维度的覆盖状态。
"""

import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer
from sqlalchemy.sql import func
from app.db.base import Base


class CoverageTrack(Base):
    """
    覆盖追踪表

    每个审计任务在每个安全维度 (D1-D10) 上有一条记录。
    状态枚举: covered (✅), shallow (⚠️), uncovered (❌), skipped
    """
    __tablename__ = "coverage_tracks"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String, ForeignKey("audit_tasks.id"), nullable=False, index=True)
    dimension = Column(String(10), nullable=False)  # D1 ~ D10
    status = Column(String(20), default="uncovered")  # covered, shallow, uncovered, skipped
    findings_count = Column(Integer, default=0)  # 该维度下的发现数
    notes = Column(Text, nullable=True)  # 备注（如 "D1: SQL注入深度追3层调用链"）

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
