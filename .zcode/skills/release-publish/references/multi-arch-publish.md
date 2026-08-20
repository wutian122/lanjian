# 多架构镜像发布配方（通用）

目标：`wutian449/<repo>:vX.Y.Z` 为 **amd64 + arm64 合并的 manifest**。
强制：**新镜像基于上一版本镜像生成**（不是从零基础镜像重建），并**必须多架构合并后才上传**。

## 1. 基于上一版本镜像的 Dockerfile

新版本的 Dockerfile 应以**上一版本镜像**为基础层：

```dockerfile
# 以已发布的上一版本镜像为基础（保证新镜像是旧镜像超集）
FROM wutian449/<repo>:v<上一版> AS runtime

# 叠加新版本代码（构建产物/虚拟环境等随 COPY 带入）
COPY ./dist 或 ./app /app

# 如有依赖变更，在构建内重装
RUN pip install --no-cache-dir -r requirements-new.txt   # 或 uv sync 等
```

- 构建机需先有上一版本镜像：`docker pull wutian449/<repo>:v<上一版>`（或本地已有）
- 保证 `docker history <新镜像>` 顶层能看到上一版本镜像的层
- 若上一版本镜像拉不到（代理/网络问题）→ 用 `proxy-config.md` 的镜像源/客户端代理绕行

## 2. 每架构构建

- amd64 构建机：`docker build -t wutian449/<repo>:vX.Y.Z .`
- arm64 构建机：同上（各自架构）
- 构建前确认基础镜像可达（上一版本镜像 + 必要的基础镜像）

## 3. 推送单架构（先打架构 tag）

```bash
docker tag wutian449/<repo>:vX.Y.Z wutian449/<repo>:vX.Y.Z-amd64
docker push wutian449/<repo>:vX.Y.Z-amd64
# arm64 机同理
docker tag wutian449/<repo>:vX.Y.Z wutian449/<repo>:vX.Y.Z-arm64
docker push wutian449/<repo>:vX.Y.Z-arm64
```

⚠️ 连接不稳（代理间歇 EOF）用重试循环：

```bash
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
  out=$(timeout 300 docker push wutian449/<repo>:vX.Y.Z-<arch> 2>&1)
  if echo "$out" | grep -q "digest:"; then
    echo "PUSH_OK attempt $attempt"; echo "$out" | tail -2; break
  else
    echo "attempt $attempt failed, retrying..."; sleep 5
  fi
done
```

⚠️ 忘了先打架构 tag 会报 `tag does not exist`（不是连接错误，别误判为连接问题）。

## 4. 合并 manifest（任一台，客户端侧需 env 代理）

```bash
export HTTP_PROXY=<代理> HTTPS_PROXY=<代理> NO_PROXY=localhost,127.0.0.1,::1

docker manifest create wutian449/<repo>:vX.Y.Z \
  --amend wutian449/<repo>:vX.Y.Z-amd64 \
  --amend wutian449/<repo>:vX.Y.Z-arm64
docker manifest push wutian449/<repo>:vX.Y.Z
```

- manifest 操作是**客户端侧**，不走 daemon 代理，必须显式 `export HTTP_PROXY/HTTPS_PROXY`
- 失败重试（连接不稳常见）

## 5. 验证（双架构确认）

```bash
# 确认 manifest 包含两个架构
docker manifest inspect wutian449/<repo>:vX.Y.Z | grep -E "architecture|digest"
# 期望同时出现 "amd64" 与 "arm64" 及其 digest

# 每架构各拉一次确认能解析
# amd64 机：
docker pull wutian449/<repo>:vX.Y.Z
# arm64 机：
docker pull wutian449/<repo>:vX.Y.Z
```

## 发布清单（多架构相关）

- [ ] 两个架构镜像均已 push（`...-amd64` / `...-arm64`，各自有 digest）
- [ ] manifest 已 create + push（`vX.Y.Z` 无架构后缀）
- [ ] `docker manifest inspect` 显示 amd64 + arm64 两条
- [ ] 新镜像基于上一版本镜像（Dockerfile `FROM ...:<上一版>` + `docker history` 可验证）
- [ ] 未残留"单架构 vX.Y.Z"（不带 -amd64/-arm64 后缀）的错误发布
