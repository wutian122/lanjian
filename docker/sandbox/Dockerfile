# lanjian Agent Sandbox
# 鐎瑰鍙忓▽娆戭唸閻滎垰顣ㄩ悽銊ょ艾濠曞繑绀婃宀冪槈閸?PoC 閹笛嗩攽
# 闂嗗棙鍨氶崥鍕鐎瑰鍙忛幍顐ｅ伎瀹搞儱鍙?(Semgrep, Bandit, Gitleaks, etc.)

FROM docker.m.daocloud.io/python:3.11-bullseye

# ?????arm64/amd64 ?????buildx ?????legacy build ? --build-arg TARGETARCH=
ARG TARGETARCH

LABEL maintainer="XCodeReviewer Team"
LABEL description="Secure sandbox environment for vulnerability verification and security scanning"

# 鐎瑰顥婇崺鐑樻拱瀹搞儱鍙?
# 鐎瑰顥婇崺鐑樻拱瀹搞儱鍙?
# 娴ｈ法鏁ら梼鍧楀櫡娴滄垿鏆呴崓蹇撳闁?apt
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    sed -i 's/security.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    netcat-openbsd \
    dnsutils \
    iputils-ping \
    ca-certificates \
    git \
    unzip \
    jq \
    php-cli \
    openjdk-11-jdk-headless \
    ruby-full \
    build-essential \
    cmake \
    clang \
    llvm \
    && rm -rf /var/lib/apt/lists/*

# 鐎瑰顥?Node.js (閻劋绨?JavaScript/TypeScript 娴狅絿鐖滈幍褑顢?閸?npm audit)
# 娴ｈ法鏁ゅǎ妯虹杺/闂冨潡鍣锋禍鎴︽殔閸嶅繐濮為柅?
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm config set registry https://registry.npmmirror.com \
    && rm -rf /var/lib/apt/lists/*

# 鐎瑰顥?Go (娴ｈ法鏁ら梼鍧楀櫡娴滄垿鏆呴崓?
ENV PATH=$PATH:/usr/local/go/bin
ENV GOPROXY=https://goproxy.cn,direct
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    curl -L https://mirrors.aliyun.com/golang/go1.21.6.linux-${TARGETARCH:-amd64}.tar.gz -o go.tar.gz && \
    tar -C /usr/local -xzf go.tar.gz && \
    rm go.tar.gz

# 鐎瑰顥?Rust (娴ｈ法鏁?rsproxy 闂€婊冨剼)
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH \
    RUSTUP_DIST_SERVER=https://rsproxy.cn \
    RUSTUP_UPDATE_ROOT=https://rsproxy.cn/rustup
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    curl --proto '=https' --tlsv1.2 -sSf https://rsproxy.cn/rustup-init.sh | sh -s -- -y --default-toolchain stable --profile minimal && \
    chmod -R a+w /usr/local/cargo

# 鐎瑰顥?Python 鐎瑰鍙忓銉ュ徔
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
    requests \
    httpx \
    aiohttp \
    beautifulsoup4 \
    lxml \
    pycryptodome \
    paramiko \
    pyjwt \
    python-jose \
    sqlparse \
    semgrep \
    bandit \
    safety

# 安全审计增强依赖库
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
    cryptography \
    passlib \
    bcrypt \
    pyyaml \
    flask \
    jinja2 \
    dnspython \
    msgpack \
    regex \
    MarkupSafe \
    itsdangerous \
    werkzeug \
    sqlalchemy \
    fastapi \
    uvicorn \
    django \
    psycopg2-binary

# Gitleaks: 检测代码中的硬编码密钥、Token、私钥
# 🔥 使用本地预下载的二进制包，避免沙箱构建时访问 GitHub
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    GL_ARCH="$([ "${TARGETARCH:-amd64}" = "arm64" ] && echo arm64 || echo x64)" && \
    curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_${GL_ARCH}.tar.gz" -o /tmp/gitleaks.tar.gz && \
    tar -xzf /tmp/gitleaks.tar.gz -C /usr/local/bin/ && \
    chmod +x /usr/local/bin/gitleaks && \
    rm /tmp/gitleaks.tar.gz

# TruffleHog: 检测密钥泄露（暂时保留，按需启用）
# RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
#     curl -sSfL https://raw.githubusercontent.com/trufflesecurity/trufflehog/main/scripts/install.sh | sh -s -- -b /usr/local/bin

# OSV-Scanner: 依赖漏洞扫描（暂时保留，按需启用）
# RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
#     curl -L https://github.com/google/osv-scanner/releases/download/v1.6.2/osv-scanner_1.6.2_linux_amd64 -o /usr/local/bin/osv-scanner && \
#     chmod +x /usr/local/bin/osv-scanner

# 閸掓稑缂撻棃?root 閻劍鍩?
RUN groupadd -g 1000 sandbox && \
    useradd -u 1000 -g sandbox -m -s /bin/bash sandbox

# 閸掓稑缂撳銉ょ稊閻╊喖缍?
RUN mkdir -p /workspace /tmp/sandbox && \
    chown -R sandbox:sandbox /workspace /tmp/sandbox

# 鐠佸墽鐤嗛悳顖氼暔閸欐﹢鍣?
ENV HOME=/home/sandbox
ENV PATH=/home/sandbox/.local/bin:$PATH
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 闂勬劕鍩?Python 鐎电厧鍙嗙捄顖氱窞
ENV PYTHONPATH=/workspace

# 閸掑洦宕查崚浼存姜 root 閻劍鍩?
USER sandbox

WORKDIR /workspace

# 姒涙顓婚崨鎴掓姢
CMD ["/bin/bash"]

