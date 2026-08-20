# ============================================
# 多阶段构建 - 构建阶段
# ============================================
FROM python:3.12-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 彻底清除代理设置
ENV no_proxy="*"
ENV NO_PROXY="*"

# 清除 apt 代理配置并配置重试，使用阿里云镜像
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true && \
    rm -f /etc/apt/apt.conf.d/proxy.conf 2>/dev/null || true && \
    echo 'Acquire::http::Proxy "false";' > /etc/apt/apt.conf.d/99-no-proxy && \
    echo 'Acquire::https::Proxy "false";' >> /etc/apt/apt.conf.d/99-no-proxy && \
    echo 'Acquire::Retries "5";' >> /etc/apt/apt.conf.d/99-no-proxy && \
    echo 'Acquire::http::Timeout "60";' >> /etc/apt/apt.conf.d/99-no-proxy

# 安装构建依赖（gcc 只在构建阶段需要）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv（pip 安装，走阿里云 PyPI；避免依赖第三方镜像源拉取 docker.io/astral/uv，
# 国内服务器网络下该 org 镜像经 daocloud/1ms 等镜像源代理不稳定）
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ uv

# 配置 uv 使用阿里云 PyPI 镜像（国内服务器访问官方 pypi.org 极慢）
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# 复制依赖文件
COPY pyproject.toml uv.lock README.md ./

# 安装 Python 依赖到虚拟环境（不使用 frozen，允许重新解析依赖源）
RUN uv sync --no-dev

# ============================================
# 多阶段构建 - 运行阶段
# ============================================
FROM python:3.12-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 彻底清除代理设置
ENV no_proxy="*"
ENV NO_PROXY="*"

# 配置使用阿里云 Debian 镜像源
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true

# 清除 apt 代理配置
RUN rm -f /etc/apt/apt.conf.d/proxy.conf 2>/dev/null || true && \
    echo 'Acquire::http::Proxy "false";' > /etc/apt/apt.conf.d/99-no-proxy && \
    echo 'Acquire::https::Proxy "false";' >> /etc/apt/apt.conf.d/99-no-proxy && \
    echo 'Acquire::Retries "5";' >> /etc/apt/apt.conf.d/99-no-proxy && \
    echo 'Acquire::http::Timeout "60";' >> /etc/apt/apt.conf.d/99-no-proxy

# 只安装运行时依赖（不需要 gcc）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        git \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libpangocairo-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libglib2.0-0 \
        shared-mime-info \
        fonts-noto-cjk \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# 从构建阶段复制虚拟环境和 uv
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv

# 复制应用代码
# 安装 Semgrep 静态分析工具
RUN pip install --no-cache-dir semgrep && semgrep --version
COPY . .

# 创建上传目录并设置启动脚本权限
RUN mkdir -p /app/uploads/zip_files && \
    chmod +x /app/docker-entrypoint.sh

# 暴露端口
EXPOSE 8000

# 启动命令 - 使用启动脚本自动执行数据库迁移
CMD ["/app/docker-entrypoint.sh"]
