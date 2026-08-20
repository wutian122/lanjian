---
name: release-publish
description: 通用软件发布流程：配置出网代理（用户指定代理服务器）→ 更新全部文档（含 GitHub 自述文件/README）→ 清理无用文件 → Git 提交推送 GitHub → 打 tag + GitHub Release 发布版本 → 多架构镜像（amd64+arm64 合并 manifest）上传 Docker Hub。强制规则：镜像必须多架构合并后才能上传（禁止单架构）；新版本镜像必须基于上一版本镜像生成。当用户说"发布版本/发版/发布这个项目/帮我把这个项目发布到 github 上/提交到 github 并发布/走一遍发布流程/更新文档清理后发布/打 tag 发 release/更新 github 自述文件/更新 README 再发布/多架构镜像上传 docker hub/镜像合并多架构/上传镜像到 docker hub/帮我配下出网代理再发布"等时使用。用户提到"发布/发版/release/提交 github/上传镜像到 docker hub/多架构镜像/更新自述文件"任一含义时都应考虑触发。
---

# 通用发布流程（Release & Publish）

把一次发布从"代码就绪"推进到"GitHub 已发布 + Docker Hub 多架构镜像已上传"的完整流程。
本 skill 是**通用**的：出网代理、仓库地址、镜像名、版本号都由用户/环境指定，按需配置。

## 强制规则（不可协商）

1. **Docker Hub 只能上传多架构 manifest**（amd64 + arm64 合并），禁止只传单架构镜像。
2. **新版本镜像必须基于上一版本镜像生成**（Dockerfile `FROM wutian449/<repo>:<上一版>`，在其上叠加新代码/变更），保证新镜像是旧镜像的超集，禁止从零基础镜像重建。
3. **出网代理由用户指定**，帮助用户配置 docker/构建链路的代理，不硬编码任何代理地址。
4. 发布前文档、清理、验证一个都不能跳；验证结果如实报告。

## 前置条件

1. 代码变更已完成并验证（测试全绿、lint/类型检查无新增问题、冒烟通过）
2. 新代码在 feature 分支上，未合并 main
3. 目标版本号确定（如 v5.3.0）；确认上一版本号（如 v5.2.0）与已发布的镜像存在
4. 远程机器访问方式与凭证就绪（本环境统一走 remote-shell 技能 + 加密凭证库）

## 流程

### Step 0 出网代理配置（用户指定代理服务器）

- 向用户确认：代理服务器地址（如 `http://<host>:<port>`）、哪些机器/链路需要走它（docker daemon / BuildKit / 构建机）
- 按 `references/proxy-config.md` 配置：
  - docker daemon：`/etc/docker/daemon.json` 加 `proxies` 段
  - systemd drop-in：`/etc/systemd/system/docker.service.d/http-proxy.conf` 加 HTTP_PROXY/HTTPS_PROXY
  - `systemctl daemon-reload && systemctl restart docker`（容器按 restart:unless-stopped 自动恢复；维护窗口）
  - 验证：`docker info | grep -i proxy` + 拉一个测试镜像
- ⚠️ 若 docker Go 客户端经代理 `EOF`（代理开流量探测导致）→ 见 `references/proxy-config.md` 的绕行方案（国内镜像源/客户端 env 代理）或提示用户关流量探测

### Step 1 更新文档（含 GitHub 自述文件）

1. **版本号同步**（典型 7 处，按项目实际核对）：
   - 后端清单（`pyproject.toml`/`package.json` 等）version 字段
   - 部署/构建清单（compose/helm/CI 里的镜像 tag）——只改本发布涉及的组件，未变更组件（如 sandbox）保持原版本
2. **GitHub 自述文件（README）必须更新**——推送到 GitHub 后仓库首页/发布页展示的就是它：
   - 版本徽章 → 新版本号
   - 镜像版本说明、部署/使用说明同步到新版本
   - 本次发布新增的能力/功能，在特性清单补充对应条目
   - 可选：CHANGELOG/发布要点段落
3. **状态段**：部署/发布状态、版本历史更新为新版本 + 日期
4. **已知过时文档**：逐条用代码核验后再改（行数、事件数、参数默认值、已删除文件引用），不凭记忆写
5. 历史交付/归档文档不改写（保留历史记录）

验证：`grep -rn "<旧版本>"` 在目标文件无残留（第三方锁文件的依赖版本除外）；README 的新版本号与新功能在推送前已在本地可见（推送到 GitHub 后仓库首页即展示最新自述文件）。

### Step 2 清理无用文件

- 删除会话临时产物（runner 脚本、误建目录副本）
- 确认 `.gitignore` 覆盖本地工具/扫描产物（如 `.mimosa/`、`.agents/` 等）
- 废弃代码：确认全仓无引用后删除
- 清理后 `git status` 应只剩有意变更

### Step 3 Git 提交 + 推送 GitHub

1. 在 feature 分支按 Conventional Commits 提交（`fix/feat/docs/chore(release)`）
2. 合并 feature → main：`git merge --no-ff <branch> -m "Merge branch '...' into main (vX.Y.Z release)"`
3. 推送：`git push origin main` + `git tag -a vX.Y.Z -m "..."` + `git push origin vX.Y.Z`
4. ⚠️ 本环境主控会话直接 `git commit` 会被 Mimosa L3 拦截器全仓扫描拦截：先跑聚焦扫描确认本次变更 0 新增发现（取 seal 证据），交由 implementer 子代理执行 git 操作；不自行绕过拦截器

### Step 4 发布版本（GitHub Release）

```bash
gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <发布说明.md>
```
发布说明用中文：主要变更（按功能分组）、部署情况、验证结果。

### Step 5 多架构镜像 → Docker Hub

**铁律**：新镜像基于上一版本镜像构建；必须 amd64+arm64 合并 manifest 上传。

1. **准备构建源**：把新版本代码打包（排除 `.venv`/`node_modules`/`dist`/缓存），传输到各架构构建机（amd64 机 + arm64 机），解压到构建目录
2. **基于上一版本镜像构建**（每架构构建机各构建一次）：
   - 构建机先确保有上一版本镜像（`docker pull wutian449/<repo>:<上一版>` 或本地已有）
   - Dockerfile 以 `FROM wutian449/<repo>:<上一版>` 为基础，`COPY` 叠加新版本代码/变更（依赖变更时在构建内重装）
   - `docker build -t wutian449/<repo>:vX.Y.Z .`
   - ⚠️ 若上一版本镜像经代理拉不到 → 用 `references/multi-arch-publish.md` 的镜像源/代理绕行方案
3. **推送单架构**（每架构 tag 后推）：
   ```
   docker tag wutian449/<repo>:vX.Y.Z wutian449/<repo>:vX.Y.Z-<arch>
   docker push wutian449/<repo>:vX.Y.Z-<arch>
   ```
   ⚠️ 连接不稳时用重试循环（输出含 `digest:` 判成功）
4. **合并 manifest**（任一台）：
   ```
   export HTTP_PROXY=<代理> HTTPS_PROXY=<代理>   # manifest 操作是客户端侧，需显式 env 代理
   docker manifest create wutian449/<repo>:vX.Y.Z --amend ...-amd64 --amend ...-arm64
   docker manifest push wutian449/<repo>:vX.Y.Z
   ```
   重试直至成功
5. **验证**：`docker manifest inspect` 确认双架构 digest；从 Hub `docker pull wutian449/<repo>:vX.Y.Z`（amd64 机验证解析 amd64、arm64 机验证 arm64）

详细配方与坑见 `references/multi-arch-publish.md`。

### Step 6 验证与收尾

- 双端验证（如已部署）：backend `/health` + 版本、前端 bundle 版本、数据库/中间件 healthy
- 汇总交付：GitHub 链接、Docker Hub manifest 链接、各架构 digest、部署验证结果
- 如实报告；有跳过/失败项明说

## 安全与铁律

- 动数据库前先 `pg_dump`；删除/重置前先保存 `.env`
- 密码不进命令行/脚本：服务器侧 `source .env` 取，或从凭证库取
- 危险操作（重启/删除/构建/推送/改 daemon.json）先经用户确认
- 只 bump 版本号不重建镜像无效；前端等版本号烘焙在构建产物的，改版本必须重建

## 常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| docker pull/build `EOF`/`connection reset` | 代理开流量探测掐 Go 客户端 | 走国内镜像源；或让用户关流量探测/换核心 |
| `docker manifest` 超时 | manifest 操作不走 daemon 代理 | 显式 `export HTTP_PROXY/HTTPS_PROXY` |
| push 间歇 `EOF` | 代理连接不稳 | 重试循环（判 `digest:`） |
| `tag does not exist` | 忘了先打架构 tag | 先 `docker tag` 再 push |
| 主控 `git commit` 被拦 | 本环境 Mimosa L3 全仓扫描 | 子代理提交 + 0 新增发现 seal 证据 |

## 参考

- `references/proxy-config.md` — 出网代理配置模板（daemon/systemd/验证/绕行）
- `references/multi-arch-publish.md` — 多架构构建/推送/manifest 配方（基于上一版镜像的 Dockerfile、重试、验证）
- `references/lanjian-example.md` — 本项目（lanjian）具体环境与实测记录（A/B 机、代理、镜像源、Mimosa）
