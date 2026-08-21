"""统一时间序列化：数据库存 UTC（timestamptz），对外输出统一北京时间（Asia/Shanghai, UTC+8）。

背景：agent 审计页面按浏览器本地时区显示时间，数据库存 UTC，两者数值相差 8 小时
造成"数据库 05:08 vs 页面 13:08"的观感不一致。本模块统一所有 API/SSE 输出为
北京时间 ISO 字符串，前端 new Date() 解析后显示的就是北京时间。
"""
from datetime import datetime, timezone, timedelta

CN_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def serialize_cst(dt: datetime | None) -> str | None:
    """把任意 datetime（含 naive/UTC/其它时区）统一输出为北京时间 ISO 字符串。

    - naive datetime 视为 UTC（后端存储约定），先补时区再转北京时间
    - None 原样返回 None
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CN_TZ).isoformat()
