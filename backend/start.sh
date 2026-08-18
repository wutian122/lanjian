#!/bin/bash
# 浣跨敤 uv 鍚姩鍚庣鏈嶅姟

set -e

echo "馃殌 鍚姩 lanjian 鍚庣鏈嶅姟..."

# 妫€鏌?uv 鏄惁瀹夎
if ! command -v uv &> /dev/null; then
    echo "鉂?鏈壘鍒?uv锛岃鍏堝畨瑁咃細"
    echo "   curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# 鍚屾渚濊禆锛堝鏋滈渶瑕侊級
if [ ! -d ".venv" ]; then
    echo "馃摝 棣栨杩愯锛屾鍦ㄥ畨瑁呬緷璧?.."
    uv sync
fi

# 杩愯鏁版嵁搴撹縼绉?
echo "馃攧 杩愯鏁版嵁搴撹縼绉?.."
uv run alembic upgrade head

# 鍚姩鏈嶅姟
echo "鉁?鍚姩鍚庣鏈嶅姟..."
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --no-access-log

