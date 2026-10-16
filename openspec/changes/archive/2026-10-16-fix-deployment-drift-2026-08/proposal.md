# 提案：fix-deployment-drift-2026-08

## 背景

2026-08-17 通过 `remote-shell` 技能对两台生产服务器（`192.168.238.11` amd64 / `10.129.7.87` arm64）做全量核对，发现蓝鉴 v5.0.0 部署存在 8 类"文档与现实脱节"缺陷：

1. 🟠 `AGENTS.md` 把"其他业务（8000 端口被 agent-compose 占用、host-override.yml）"错记到了 A 机。实际 A 机 8000 直对外暴露，host-override.yml 不存在；B 机才有"其他业务"（drone-platform 反代 8080）。
2. 🟠 `docker-compose.prod.yml` 的 backend service 缺 `/tmp/lanjian:/tmp/lanjian:rw` bind mount，一旦切到 prod compose，沙箱验证全空跑。
3. 🟠 A 机（arm64）33 天未重启。
4. 🟡 B 机现场跑的是 2707B 精简版 compose（未落仓）：db 加 `seccomp:unconfined`、锁 `v5.0.0`、删除 P0 注释。
5. 🟡 A 机有孤儿网络 `lanjian_default`（无容器挂载）。
6. 🟡 A 机 `.env` 未显式设 `IMAGE_TAG=v5.0.0`，靠镜像层缓存副作用。
7. 🟢 B 机 sandbox 镜像 7 周前构建（5.12GB），A 机 3 周前（2.77GB），digest 不一致。
8. 🟢 两台机 db/redis 端口 `127.0.0.1` 暴露给宿主机（攻击面 + 1）。

## 目标

把仓库编排 / 文档 / 远端部署重新对齐为单一事实源：

- `docker-compose.yml` + `docker-compose.prod.yml` + `docker-compose.b-amd64.yml` 三件套都锁 `v5.0.0`、都含 `/tmp/lanjian` bind mount。
- `AGENTS.md` 部署段与远端实际状态一致。
- 远端执行结果回归后通过。

## 范围

- 修改 4 个文件：`docker-compose.yml` / `docker-compose.prod.yml` / `AGENTS.md` / `openspec/changes/fix-deployment-drift-2026-08/specs/deployment/spec.md`。
- 新建 2 个文件：`docker-compose.b-amd64.yml` / `openspec/changes/fix-deployment-drift-2026-08/{proposal,tasks}.md`。
- 远端操作：2 台机的 docker network / .env / 沙箱镜像 buildx / recreate 容器。

## 非目标

- 不新增 alembic 迁移。
- 不升级 backend / frontend / sandbox 镜像版本（仍 `v5.0.0`）。
- 不改 backend / frontend / nginx 业务代码。
- 不做蓝鉴的负载均衡 / 高可用改造。
- 不重做安全加固（P0-1/2/3/4 等已在 2026-07 交付完成）。
- 不动 drone-platform 反代配置（B 机 8080）。
- 不动 A 机的宿主机 xrdp / Xvnc / xray 运维工具。
