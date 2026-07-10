"""
全局中间件
"""
from starlette.middleware.base import BaseHTTPMiddleware


class AppNameMiddleware(BaseHTTPMiddleware):
    """在所有 API 响应中添加 X-App-Name 头"""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-App-Name"] = "Lanjian"
        return response
