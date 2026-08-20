# version-bump

## Purpose

The version-bump capability documents the published behavior for users and maintainers.

## Requirements

### Requirement: 全仓版本号统一为 5.3.0

The system SHALL 将后端 pyproject、前端 package.json、三份部署 compose（backend/frontend 镜像）、
README 与根 AGENTS.md 中的版本号从 5.2.0 同步为 5.3.0；
sandbox 镜像 SHALL 保持 v5.1.0（本次无沙箱改动）。

#### Scenario: 清单解析验证

- **WHEN** 解析 `backend/pyproject.toml` 与 `frontend/package.json`
- **THEN** 两者的 version 字段均为 "5.3.0"

#### Scenario: 无残留旧版本号

- **WHEN** 在 7 个目标文件中搜索 `5.2.0` / `v5.2.0`
- **THEN** 无残留命中（第三方 `pnpm-lock.yaml` 依赖版本除外）

#### Scenario: sandbox 镜像版本保持

- **WHEN** 检查三份 compose 的 sandbox 镜像引用
- **THEN** 仍为 `wutian449/lanjian-sandbox:v5.1.0`
