# 任务清单：fix-deployment-drift-2026-08

## 本地仓库改动

- [x] **T1** `docker-compose.prod.yml` backend 加 `/tmp/lanjian:/tmp/lanjian:rw` bind mount — `done @ 2026-08-17`
- [x] **T2** `docker-compose.yml` 锁 4 处镜像到 `v5.0.0`、去除 `${IMAGE_TAG:-latest}` 浮动 — `done @ 2026-08-17`
- [x] **T3** 新建 `docker-compose.b-amd64.yml`（独立精简版，B 现场 override 落仓）— `done @ 2026-08-17`
- [x] **T4** `AGENTS.md` "生产部署"段落重写（A/B 业务错位纠正）— `done @ 2026-08-17`

## 远端操作

- [x] **T5** OpenSpec change `fix-deployment-drift-2026-08` 创建（proposal / tasks / spec 三个文件已写）— `done @ 2026-08-17`
- [ ] **T6** `10.129.7.87` (arm) `docker network rm lanjian_default`
- [ ] **T7** `10.129.7.87` (arm) `.env` 显式追加 `IMAGE_TAG=v5.0.0`
- [ ] **T11** `192.168.238.11` (amd) 本地 `docker buildx build` 沙箱镜像 + recreate
- [ ] **T12** `10.129.7.87` (arm) 本地 `docker buildx build` 沙箱镜像 + recreate

## 整体回归

- [ ] **T9** 两台机整体回归：alembic `023_drop_dead` / `/health` 200 / 5 容器 / 网络无孤儿 / 沙箱 image digest 比对
- [ ] **T10** `10.129.7.87` (arm) 排维护窗口重启（**R-5：reboot 前再向老板确认**）

## 已完成概要

- T1：prod compose 加 bind mount（防沙箱空跑）
- T2：default compose 锁 `v5.0.0`（防 latest 漂移）
- T3：B 机 override 落仓（防现场漂移失控）
- T4：AGENTS.md 部署段对齐实际（A/B 业务错位纠正）
- T5：OpenSpec 留痕（5 个 MODIFIED Requirements）
