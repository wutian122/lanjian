#!/usr/bin/env bash
# 在 lanjian-backend 容器内运行 pytest（工作区挂载；容器 venv 含运行依赖，
# dev 依赖每次临时补装）。本机 .venv 为 Windows 产物不可用，勿覆盖。
# 挂载整个 lanjian 仓库到 /repo（compose 类测试按仓库根路径查找文件），
# PYTHONPATH 指向 /repo/backend。
# 用法: ./run_pytest.sh [pytest 参数...]  （默认全量 -q）
set -eo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
docker run --rm -v "$REPO:/repo" -w /repo/backend \
  -e LANJIAN_TEST_DUMMY_KEY=unit-test-dummy-key -e PYTHONDONTWRITEBYTECODE=1 \
  --entrypoint "" "${LANJIAN_TEST_IMAGE:-wutian449/lanjian-backend:v6.6.0}" sh -c \
  '/app/.venv/bin/pip install -q --disable-pip-version-check pytest pytest-asyncio pytest-cov >/dev/null 2>&1; PYTHONPATH=/repo/backend /app/.venv/bin/python -m pytest "$@"' -- "$@"
