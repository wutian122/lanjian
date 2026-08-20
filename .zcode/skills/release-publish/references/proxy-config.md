# 出网代理配置（通用模板）

用户指定代理服务器（如 `http://<proxy-host>:<port>`）。本文件是配置与排错配方，所有地址由用户提供。

## 适用：Linux 服务器 docker 出网

### 1) `/etc/docker/daemon.json` 加 `proxies` 段
```json
{
  "proxies": {
    "http-proxy": "http://<proxy-host>:<port>",
    "https-proxy": "http://<proxy-host>:<port>",
    "no-proxy": "localhost,127.0.0.1,::1"
  }
}
```

### 2) systemd drop-in `/etc/systemd/system/docker.service.d/http-proxy.conf`
```ini
[Service]
Environment="HTTP_PROXY=http://<proxy-host>:<port>"
Environment="HTTPS_PROXY=http://<proxy-host>:<port>"
Environment="NO_PROXY=localhost,127.0.0.1,::1,db,redis"
```

### 3) 生效
```bash
systemctl daemon-reload && systemctl restart docker
```
⚠️ docker 重启会连带重启容器（restart: unless-stopped 自动恢复，db/redis 数据卷不受影响），建议维护窗口。

### 4) 验证
```bash
docker info | grep -i proxy   # 期望 HTTP Proxy: http://<proxy-host>:<port>
docker pull <一个测试镜像>       # daemon 拉取走代理
```

## 关键坑：docker Go 客户端经代理 EOF

- **现象**：`docker pull`/`docker build` 报 `EOF` / `connection reset`；`Head "https://registry-1.docker.io/...": EOF`
- **根因**：代理（如 sing-box/v2ray 等）开启"流量探测/sniffing"时，掐断 docker Go 客户端连接；HTTP/1.1 客户端（curl/python）通常正常
- **根治**：让用户在代理软件里**关闭流量探测**（或换核心，如 xray）；不要用 JSON 往返编辑代理配置（会崩配置）
- **绕行**：
  - **国内/内网镜像源**：`docker pull docker.m.daocloud.io/library/<img>:<tag>` 等（拉取后 `docker tag` 成无前缀 tag 供 build 用）
  - **客户端 env 代理**（BuildKit 与 manifest 操作不走 daemon 代理）：
    ```bash
    export HTTP_PROXY=http://<proxy-host>:<port> HTTPS_PROXY=http://<proxy-host>:<port>
    ```
- **验证连通性**：用 `curl -x <代理> https://registry-1.docker.io/v2/`（401 = 通）；不要用 ping（ICMP 不走代理）

## 注意事项

- BuildKit 构建内部如需拉 registry，用客户端 env 代理；构建内的包管理（pip/npm/apt）建议配国内镜像（如阿里云 PyPI / npmmirror），彻底避免代理依赖
- `registry-mirrors` 配置在某些 docker 版本不生效（`docker info` 显示为空）——不要依赖，用显式镜像源 pull
- 各机器到各镜像源的连通性可能不同，逐个实测
