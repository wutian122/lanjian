# Design：fix-deployment-drift-2026-08

本 change 涉及纯**部署对齐**（编排 / 文档 / 远端配置），无独立架构设计。

## 决策

1. **三件套 compose 文件**：保留仓库 `docker-compose.yml`（默认）、`docker-compose.prod.yml`（生产）、新增 `docker-compose.b-amd64.yml`（B 现场 override）。三者**相互独立、不 include**，避免链式漂移。

2. **image tag 锁定**：所有 `wutian449/lanjian-*` 镜像统一显式 `v5.0.0`，禁止 `${IMAGE_TAG:-latest}`。升级需先 git tag + 双机同步验证。

3. **沙箱镜像本地 buildx**：每台机本地 `docker buildx build` 沙箱镜像，**不** push Docker Hub，避免凭据依赖。本地 tag 覆盖现有 `v5.0.0` 层，跨机 digest 允许不同（架构不同）。

4. **AGENTS.md 部署段与远端同步**：以"IP + 架构"为命名基准（amd / arm），不沿用历史 A/B 标签（命名曾经错位）。

## 不变更

- 不动 alembic 迁移。
- 不动 backend / frontend / nginx 业务代码。
- 不重做 2026-07 安全加固。
- 不动 B 机 drone-platform 反代 / A 机 xrdp / Xvnc / xray。
