# Deployment Spec Delta — fix-deployment-drift-2026-08

## MODIFIED Requirements

### Requirement: Compose files must be byte-traceable between repo and server (drift-free)

蓝鉴部署的所有 compose 文件（默认 + prod + 现场 override）在仓库内必须存在，远端 `/root/lanjian/` 必须是仓库内某 compose 的精确副本，**不允许有"现场手工裁剪"未落仓的版本**。

#### Scenario: B server compose drift

- WHEN 老板在 B 机 `192.168.238.11` 手工修改 `/root/lanjian/docker-compose.yml`
- THEN 必须在 24 小时内反向同步进 `docker-compose.b-amd64.yml` 命名 override
- AND 仓库内必须有该 override 文件的备份

#### Scenario: A server using unmodified default compose

- WHEN A 机 `10.129.7.87` 用仓库默认 `docker-compose.yml` 启动
- THEN `docker compose config` 输出与仓库 `docker-compose.yml` 哈希一致
- AND 没有未在仓库内的现场挂载/环境变量

### Requirement: Production compose must include sandbox bind mount

`docker-compose.prod.yml` 的 backend service 必须包含 `- /tmp/lanjian:/tmp/lanjian:rw`，与默认 `docker-compose.yml` 一致。

#### Scenario: Prod compose missing mount

- WHEN 老板用 `docker compose -f docker-compose.prod.yml up -d` 启动
- THEN backend 容器必须能 mount `/tmp/lanjian` 到宿主机
- AND sandbox 容器能看到 backend 解压的 ZIP 产物
- AND `docker inspect lanjian-backend-1 --format '{{range .Mounts}}{{.Source}}{{.Destination}}{{end}}'` 含 `/tmp/lanjian/tmp/lanjian`

### Requirement: Images must be locked to explicit version tag

所有编排文件的蓝鉴镜像（backend / frontend / sandbox）必须使用**显式版本 tag**（如 `:v5.0.0`），**禁止**使用 `:latest` 浮动 tag 或 `${IMAGE_TAG:-latest}` 兜底。

#### Scenario: Image pull drift

- WHEN 环境变量 `IMAGE_TAG` 未设置
- THEN 编排文件必须仍然能 pull 到预期的 `v5.0.0` 镜像
- AND 静默回退到 `:latest` 是禁止的

#### Scenario: Tag locked at multiple files

- WHEN 检查 `docker-compose.yml` / `docker-compose.prod.yml` / `docker-compose.b-amd64.yml` 三个文件
- THEN 三个文件中所有 `wutian449/lanjian-*` 镜像 tag 必须**全部**是 `v5.0.0`
- AND 任何 `:latest` 出现必须被 plan `fix-deployment-drift-2026-08` 视为回归

### Requirement: AGENTS.md production section must reflect actual state

`AGENTS.md` "生产部署"段落必须与 `remote-shell` 现场核对的实际状态一致。任何服务器 IP / 端口 / 业务 / 编排文件路径的描述，在写入后 30 天内必须复检一次。

#### Scenario: Doc reality drift

- WHEN 老板对照 `AGENTS.md` 与 `ss -tlnp` 输出
- THEN 表格的每一行必须能在现场找到对应证据
- AND 错配（如"server A 8000 端口被 agent-compose 占用"）必须在发现 24 小时内修复

#### Scenario: Server business attribution

- WHEN 文档说"某台服务器有 X 业务"
- THEN 该业务的 nginx / docker 进程必须确实在那台机的 `ss -tlnp` 输出中
- AND 不允许文档把 B 机的业务错记到 A 机（或反之）

### Requirement: Sandbox image must be reproducible on each server (buildx multi-arch)

每台生产服务器必须能通过本地 `docker buildx build` 从仓库 `docker/sandbox/Dockerfile` + `seccomp.json` 复现 `wutian449/lanjian-sandbox:v5.0.0` 镜像。**不**依赖 Docker Hub 上的现成镜像。

#### Scenario: Reproducible build

- WHEN 老板在 B 机 `192.168.238.11` 执行 `docker buildx build --platform linux/amd64 -t wutian449/lanjian-sandbox:v5.0.0 .`
- THEN 构建成功
- AND 镜像 tag 与默认 compose / prod compose / B override 中的引用一致
- AND recreate 沙箱容器后 PoC 验证仍能跑通

#### Scenario: A and B build independently

- WHEN 老板在 A 机 `10.129.7.87` 用 `--platform linux/arm64` 单独 build
- AND 老板在 B 机 `192.168.238.11` 用 `--platform linux/amd64` 单独 build
- THEN 两台机的沙箱镜像 tag 都是 `wutian449/lanjian-sandbox:v5.0.0`
- AND 两台机的 `docker run wutian449/lanjian-sandbox:v5.0.0 uname -m` 分别返回 `aarch64` / `x86_64`
- AND 跨机 image digest 不要求一致（架构不同）

## REMOVED Requirements

无。

## ADDED Requirements

无（仅修改已有 Requirements）。
