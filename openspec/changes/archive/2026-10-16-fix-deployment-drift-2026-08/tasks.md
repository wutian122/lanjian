# 任务清单：fix-deployment-drift-2026-08

> **台账治理裁定（2026-10-16，老板批准弃权归档）**：T6/T7/T9/T10/T11/T12 六项未执行。
> T11 操作对象 192.168.238.11（旧 B 机）已于 2026-09 整体下线；T6/T7/T12/T9/T10 所诉求的
> 状态（镜像锁版本、网络清理、沙箱镜像重建、整体回归）已被后续演进覆盖并超越——B 机迁移
> 后 compose 体系重做（2026-09-10 v6.5.0 四服务精简版）、镜像锁版本实践延续（v6.9.x）、
> 双机部署实况多次 SSH 实测核实（2026-09-26，见根 AGENTS.md 部署段）。六项不再补做。
> **spec delta 处置**：delta 为 2026-08 部署状态的 MODIFIED 操作，但主 specs 无 deployment
> 域（新 spec 仅允许 ADDED），且其内容（192.168.238.11 现场约束等）已随旧机下线过时、
> 部署契约实际由根 AGENTS.md 部署段承载——按 `--skip-specs` 归档，不将过时规格写入主 specs。

## 本地仓库改动

- [x] **T1** `docker-compose.prod.yml` backend 加 `/tmp/lanjian:/tmp/lanjian:rw` bind mount — `done @ 2026-08-17`
- [x] **T2** `docker-compose.yml` 锁 4 处镜像到 `v5.0.0`、去除 `${IMAGE_TAG:-latest}` 浮动 — `done @ 2026-08-17`
- [x] **T3** 新建 `docker-compose.b-amd64.yml`（独立精简版，B 现场 override 落仓）— `done @ 2026-08-17`
- [x] **T4** `AGENTS.md` "生产部署"段落重写（A/B 业务错位纠正）— `done @ 2026-08-17`

## 远端操作

- [x] **T5** OpenSpec change `fix-deployment-drift-2026-08` 创建（proposal / tasks / spec 三个文件已写）— `done @ 2026-08-17`
- [x] **T6** `10.129.7.87` (arm) `docker network rm lanjian_default` — `2026-10-16 弃权（见头部裁定）`
- [x] **T7** `10.129.7.87` (arm) `.env` 显式追加 `IMAGE_TAG=v5.0.0` — `2026-10-16 弃权（锁版本实践已由后续 compose 演进覆盖）`
- [x] **T11** `192.168.238.11` (amd) 本地 `docker buildx build` 沙箱镜像 + recreate — `2026-10-16 弃权（操作对象旧机已下线）`
- [x] **T12** `10.129.7.87` (arm) 本地 `docker buildx build` 沙箱镜像 + recreate — `2026-10-16 弃权（已被后续部署演进覆盖）`

## 整体回归

- [x] **T9** 两台机整体回归：alembic `023_drop_dead` / `/health` 200 / 5 容器 / 网络无孤儿 / 沙箱 image digest 比对 — `2026-10-16 弃权（2026-09-26 双机 SSH 实测核实已取代本项回归）`
- [x] **T10** `10.129.7.87` (arm) 排维护窗口重启（**R-5：reboot 前再向老板确认**） — `2026-10-16 弃权（部署拓扑已多次变更，重启窗口诉求过时）`

## 已完成概要

- T1：prod compose 加 bind mount（防沙箱空跑）
- T2：default compose 锁 `v5.0.0`（防 latest 漂移）
- T3：B 机 override 落仓（防现场漂移失控）
- T4：AGENTS.md 部署段对齐实际（A/B 业务错位纠正）
- T5：OpenSpec 留痕（5 个 MODIFIED Requirements）
