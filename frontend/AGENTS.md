# Frontend — lanjian React TypeScript 前端

React 18，TypeScript 5.7，Vite 5，Radix UI + Tailwind CSS + shadcn/ui。

## 技术栈

| 类别 | 技术 | 备注 |
|------|------|------|
| 框架 | React 18 | 函数组件 + Hooks |
| 语言 | TypeScript 5.7 | 严格模式 |
| 构建 | Vite 5 + @vitejs/plugin-react | HMR 开发服务器 |
| 路由 | react-router-dom 6.30 | 客户端 SPA 路由 |
| 样式 | Tailwind CSS 3 + Radix UI + shadcn/ui | 实用优先 + 无头 UI |
| 状态 | React Context + useReducer | 无 Zustand |
| 图表 | Recharts 2.15 | 仪表盘图表 |
| 表单 | 原生 HTML 表单 | zod 无依赖无引用；react-hook-form 仅 shadcn 的 components/ui/form.tsx 模板引用，零业务使用 |
| HTTP | Axios + fetch | Axios 用于 API，fetch 用于 SSE |
| SSE | 自定义 parseSSE | Agent 流式输出 |
| 主题 | next-themes | 明暗主题切换 |
| 通知 | Sonner | Toast 通知 |
| SEO | react-helmet-async | 页面元数据 |
| Lint | Biome 2.2 + tsgo + ast-grep | 三层 lint |

## 目录结构

```
frontend/
├── src/
│   ├── app/                      # 应用根组件 + 路由配置（14 条路由）
│   │   ├── main.tsx              # 入口（ThemeProvider + AuthProvider + HelmetProvider）
│   │   ├── App.tsx               # 根组件（BrowserRouter + Routes）
│   │   ├── routes.tsx            # 路由配置表（13 条路由，含隐藏路由）
│   │   └── ProtectedRoute.tsx    # 路由守卫（未认证→/login，已认证 / → /dashboard，无角色分流）
│   ├── pages/                    # 路由页面
│   │   ├── AgentAudit/           # ⭐ Agent 审计 UI（最复杂页面，模块化拆分）
│   │   │   ├── index.tsx         # 主页面入口
│   │   │   ├── types.ts          # 类型定义（State、Action、Props）
│   │   │   ├── constants.tsx     # 视觉配置（颜色、图标、轮询间隔）
│   │   │   ├── utils.ts          # 工具函数（树构建、日志过滤、AI 上下文摘要）
│   │   │   ├── hooks/
│   │   │   │   ├── useAgentAuditState.ts  # 核心状态 Hook（useReducer，23 个 case）
│   │   │   │   └── useResilientStream.ts  # SSE 弹性流 Hook（604 行）
│   │   │   └── components/
│   │   │       ├── Header.tsx            # 页头（任务状态、取消/导出/新建）
│   │   │       ├── SplashScreen.tsx      # 欢迎屏
│   │   │       ├── LogEntry.tsx          # 日志条目渲染
│   │   │       ├── AgentTreeNode.tsx     # Agent 树节点
│   │   │       ├── AgentDetailPanel.tsx  # Agent 详情面板
│   │   │       ├── StatsPanel.tsx        # 统计面板
│   │   │       ├── AICollaborationPanel.tsx # AI 协作面板
│   │   │       ├── ConnectionStatus.tsx  # SSE 连接状态
│   │   │       ├── AgentErrorBoundary.tsx # 错误边界
│   │   │       └── StatusBadge.tsx       # 状态徽章
│   │   ├── AI/                   # AI 全局控制中心
│   │   │   ├── index.tsx         # 主页面
│   │   │   └── components/
│   │   │       ├── ChatWorkspace.tsx     # 聊天工作区
│   │   │       ├── SessionList.tsx       # 会话列表
│   │   │       └── TaskReferencePanel.tsx # 任务引用面板
│   │   ├── project-detail/       # 项目详情
│   │   │   ├── components/
│   │   │   │   ├── ProjectIssuesTab.tsx  # 问题列表
│   │   │   │   ├── ProjectStatsCards.tsx # 统计卡片
│   │   │   │   └── ProjectTasksTab.tsx   # 任务列表
│   │   │   └── constants.ts      # 常量
│   │   ├── Dashboard.tsx         # 仪表盘（安全态势概览）
│   │   ├── Projects.tsx          # 项目列表
│   │   ├── ProjectDetail.tsx     # 项目详情
│   │   ├── InstantAnalysis.tsx   # 即时分析
│   │   ├── AuditTasks.tsx        # 审计任务列表
│   │   ├── TaskDetail.tsx        # 任务详情
│   │   ├── AuditRules.tsx        # 审计规则管理
│   │   ├── PromptManager.tsx     # 提示词模板管理
│   │   ├── AdminDashboard.tsx    # 系统管理
│   │   ├── Account.tsx           # 账号管理
│   │   ├── RecycleBin.tsx        # 回收站
│   │   ├── Login.tsx             # 登录
│   │   └── NotFound.tsx          # 404
│   ├── components/               # UI 组件
│   │   ├── ui/                   # shadcn/ui 基础组件（37 个）
│   │   │   ├── 布局：card, dialog, sheet, separator, scroll-area
│   │   │   ├── 表单：input, textarea, select, checkbox, radio-group, switch, form
│   │   │   ├── 导航：sidebar, tabs
│   │   │   ├── 数据展示：table, badge, avatar, skeleton, progress, accordion
│   │   │   ├── 反馈：toast, alert, alert-dialog, sonner, tooltip, popover
│   │   │   ├── 交互：button, dropdown-menu
│   │   │   └── 业务扩展：metric-card, section-panel, status-badge, branch-selector, multi-select
│   │   ├── agent/                # Agent 相关组件
│   │   │   ├── CreateAgentTaskDialog.tsx  # Agent 审计任务创建
│   │   │   ├── AgentModeSelector.tsx      # 审计模式选择
│   │   │   └── EmbeddingConfig.tsx        # Embedding 配置
│   │   ├── audit/                # 审计 UI 组件
│   │   │   ├── CreateTaskDialog.tsx       # 普通审计任务创建
│   │   │   ├── FileSelectionDialog.tsx    # 文件选择
│   │   │   ├── TerminalProgressDialog.tsx # 终端进度
│   │   │   └── hooks/                    # 审计 Hooks
│   │   ├── layout/               # 布局组件
│   │   │   ├── AppShell.tsx      # 主布局壳（Sidebar + 内容区）
│   │   │   ├── Sidebar.tsx       # 侧栏导航（可折叠、移动端适配）
│   │   │   ├── MobileTopBar.tsx  # 移动端顶栏
│   │   │   ├── PageHeader.tsx    # 页面标题头
│   │   │   └── PageMeta.tsx      # SEO 元数据（Helmet）
│   │   ├── reports/              # 报告导出组件
│   │   │   ├── AgentReportExportDialog.tsx
│   │   │   ├── ExportReportDialog.tsx
│   │   │   └── InstantExportDialog.tsx
│   │   ├── database/             # 数据库管理组件
│   │   ├── system/               # 系统配置组件
│   │   ├── analysis/             # 分析相关组件
│   │   ├── common/               # 通用组件（EmptyState、ErrorBoundary）
│   ├── features/                 # 功能模块（业务服务封装）
│   │   ├── analysis/services/    # 即时代码分析引擎
│   │   ├── projects/services/    # 仓库审计、ZIP 扫描
│   │   └── reports/services/     # 报告导出（JSON/PDF）
│   ├── shared/
│   │   ├── api/                  # API 客户端
│   │   │   ├── serverClient.ts   # Axios 实例（baseURL /api/v1，请求/响应拦截器）
│   │   │   ├── agentTasks.ts     # Agent 任务 CRUD、Findings、Events、Tree、Report
│   │   │   ├── agentStream.ts    # SSE 流处理类（30+ 事件类型）
│   │   │   ├── database.ts       # 统一数据层（Profile/Project/AuditTask 等 CRUD）
│   │   │   ├── rules.ts          # 审计规则集 CRUD
│   │   │   ├── prompts.ts        # 提示词模板 CRUD + 测试
│   │   │   └── sshKeys.ts        # SSH 密钥管理
│   │   ├── config/               # 环境配置
│   │   ├── constants/            # 常量（超时、并发数等）
│   │   ├── context/              # React Context
│   │   │   ├── AuthContext.tsx    # 认证（user、isAuthenticated、login、logout）
│   │   │   └── ChatContext.tsx    # AI 聊天消息
│   │   ├── hooks/                # 自定义 Hooks
│   │   │   ├── useAsync.ts       # 异步操作状态
│   │   │   ├── useDebounce.ts    # 防抖
│   │   │   ├── useLocalStorage.ts # localStorage 持久化
│   │   │   ├── use-toast.tsx     # Toast 通知
│   │   │   └── use-mobile.ts     # 移动端检测
│   │   ├── services/             # 任务控制服务
│   │   │   └── taskControl.ts    # 任务取消/重启控制
│   │   ├── types/                # 共享类型定义
│   │   └── utils/                # 工具函数
│   │       ├── apiInterceptor.ts # 遗留代码（Axios 拦截器，性能监控 + 慢请求告警；未接线、无调用方）
│   │       ├── errorHandler.ts   # 统一错误处理（分类 + toast + 日志）
│   │       ├── fetchWrapper.ts   # 原生 fetch 包装
│   │       ├── logger.ts         # 日志系统（5 级、4 类别、localStorage 持久化）
│   │       ├── performanceMonitor.ts # 性能监控
│   │       ├── projectUtils.ts   # 项目类型判断
│   │       ├── uiText.ts         # UI 文案映射
│   │       ├── zipStorage.ts     # ZIP 文件存储
│   │       └── utils.ts          # 通用工具（cn className 合并）
│   ├── assets/                   # 静态资源
│   └── global.d.ts               # 全局类型声明
├── public/                       # 公共静态资源
├── dist/                         # 构建输出
├── package.json                  # 依赖 + 脚本
├── vite.config.ts                # Vite 配置（代理到后端 :8000）
├── tailwind.config.js            # Tailwind 自定义主题
├── tsconfig.json                 # TypeScript 配置
├── tsconfig.check.json           # tsgo 类型检查专用配置
├── tsconfig.app.json             # 应用 TypeScript 配置
├── tsconfig.node.json            # Node TypeScript 配置
├── components.json               # shadcn/ui 配置
├── postcss.config.js             # PostCSS 配置
├── nginx.conf                    # 生产 Nginx 配置
├── Dockerfile                    # 前端 Docker 镜像
├── docker-entrypoint.sh          # 容器入口脚本
├── index.html                    # HTML 入口
└── AGENTS.md                     # 本文件
```

## 路由结构

| 路径 | 组件 | 导航可见 | 说明 |
|------|------|---------|------|
| `/dashboard` | Dashboard | ✅ | 仪表盘（统计 + 趋势） |
| `/projects` | Projects | ✅ | 项目列表管理 |
| `/projects/:id` | ProjectDetail | ❌ | 项目详情（隐藏路由） |
| `/instant-analysis` | InstantAnalysis | ✅ | 即时代码分析 |
| `/audit-tasks` | AuditTasks | ✅ | 审计任务列表 |
| `/tasks/:id` | TaskDetail | ❌ | 任务详情（隐藏路由） |
| `/agent-audit/:taskId` | AgentAudit | ❌ | ⭐ Agent 审计核心页面（隐藏） |
| `/ai` | AIPage | ✅ | AI 审计控制中心 |
| `/audit-rules` | AuditRules | ✅ | 审计规则管理 |
| `/prompts` | PromptManager | ✅ | 提示词模板管理 |
| `/admin` | AdminDashboard | ✅ | 系统管理 |
| `/recycle-bin` | RecycleBin | ✅ | 回收站 |
| `/account` | Account | ❌ | 账号管理（侧栏底部入口） |
| `/login` | Login | - | 登录（公开页面） |

## SSE 流式连接（useResilientStream）

```
useResilientStream(taskId, options)
  ├── 连接管理（connect / disconnect / resetConnection）
  ├── 心跳监控（heartbeatTimeout: 45s）
  ├── 自动重连（指数退避 1s→30s + 抖动系数 0.3）
  ├── 最大重试（5 次，达到后触发 onMaxRetriesReached）
  ├── 单实例锁（模块级 activeStreams Map 防止重复连接）
  └── 状态暴露（connectionState / isConnected / error）
```

连接状态机：
```
disconnected → connecting → connected
                    ↓           ↓
              reconnecting ←── (心跳超时 / 连接错误)
                    ↓
                 failed (超过 maxReconnectAttempts)
```

关键能力：
- 长操作心跳放宽：semgrep 等外部工具调用期间心跳阈值放宽至 **180s**（普通 45s）
- 高水位续传：请求携带 `Last-Event-ID` header + `after_sequence`，重连时 sequence **不清零**，从断点续传
- paused 状态感知：任务暂停状态可感知，避免误判为连接异常

## AgentAudit 页面状态管理

采用 `useReducer` 集中状态（23 个 case）：

- `SET_TASK` / `SET_FINDINGS` / `ADD_FINDING` — 任务和发现
- `SET_AGENT_TREE` — Agent 树结构
- `ADD_LOG` / `UPDATE_LOG` / `COMPLETE_TOOL_LOG` / `REMOVE_LOG` — 日志流
- `UPDATE_OR_ADD_PROGRESS_LOG` — 进度更新
- `SELECT_AGENT` — Agent 选择
- `SET_CONNECTION_STATUS` — SSE 连接状态
- `SET_AUTO_SCROLL` / `TOGGLE_LOG_EXPANDED` — UI 状态
- `RESET` — 重置

自适应轮询策略：
- Phase 1：2s 间隔（前 2 分钟）
- Phase 2：5s 间隔（2-5 分钟）
- Phase 3：15s 间隔（5-15 分钟）
- Phase 4：60s 间隔（15 分钟后）

## 认证与权限

```
Login → POST /auth/login → 获取 token → AuthContext
  ├── rememberMe → localStorage
  └── 否则 → sessionStorage

ProtectedRoute
  ├── !isAuthenticated → /login
  └── 已认证（任意角色）+ / → /dashboard（ProtectedRoute.tsx:16-19，无角色分流）
```

前端仅做登录校验，无细粒度路由守卫，权限控制依赖后端 API。

## API 调用层

```
页面组件
  ├── 业务 API（agentTasks.ts, rules.ts, prompts.ts, sshKeys.ts）
  ├── 统一数据层（database.ts）
  └── 功能服务（features/）
        │
        ▼
  Axios 实例（serverClient.ts）
    ├── 请求拦截器：自动注入 Bearer token
    └── 响应拦截器：401 → 清除 token → 跳转 /login
```

错误处理链：
```
fetchWrapper.ts → 拦截原生 fetch，失败时记录日志
serverClient.ts → Axios 401 自动登出（自建 Axios 实例，内置请求/响应拦截器）
errorHandler.ts → 统一错误分类 + toast 通知
apiInterceptor.ts → 未接线、无调用方（serverClient.ts 自建实例，不走 apiInterceptor）
```

## 编码规范

- **类型安全**: TypeScript 严格模式，禁止 `as any`、`@ts-ignore`
- **Lint**: Biome（correctness/noUndeclaredDependencies）+ tsgo（原生类型检查）+ ast-grep（安全规则扫描）
- **格式化**: Biome（`biome format --write .`）
- **组件约定**: shadcn/ui 模式 — 基础组件在 `components/ui/`，功能组件在对应子目录
- **状态管理**: React Context（全局）+ useReducer（页面级），无 Zustand
- **样式**: Tailwind 实用类 + `cn()` 合并（clsx + tailwind-merge）

## 反模式

- **禁止 `as any`**: 类型断言必须合法
- **禁止未声明的依赖**: Biome `correctness/noUndeclaredDependencies` 检查
- **禁止直接操作 DOM**: 使用 React refs 和 Radix UI 组件
- **页面状态不可存储在组件内**: 使用 Context 或 URL 参数
- **lint 使用 `tsconfig.check.json`** 而非 `tsconfig.json`

## 常用命令

```bash
pnpm install          # 安装依赖
pnpm dev              # 开发服务器（:5173）
pnpm build            # 生产构建 → dist/
pnpm preview          # 预览生产构建
pnpm lint             # Biome + tsgo + ast-grep
pnpm type-check       # tsc --noEmit
pnpm format           # Biome 格式化
```

## 查询索引

| 任务 | 位置 | 备注 |
|------|------|------|
| Agent 审计 UI | `pages/AgentAudit/` | SSE 流式审计日志 |
| SSE 连接 | `pages/AgentAudit/hooks/useResilientStream.ts` | 弹性流 |
| Agent 审计状态 | `pages/AgentAudit/hooks/useAgentAuditState.ts` | useReducer |
| 项目详情 | `pages/project-detail/` | 项目仪表盘 + 漏洞列表 |
| 仪表盘 | `pages/Dashboard.tsx` | 安全态势概览 |
| AI 控制中心 | `pages/AI/` | 全局 AI 对话 |
| 认证 | `shared/context/AuthContext.tsx` | 登录/登出/Token 管理 |
| API 客户端 | `shared/api/` | Axios 实例 + 业务 API |
| 错误处理 | `shared/utils/errorHandler.ts` | 统一错误分类 |
| 日志系统 | `shared/utils/logger.ts` | 5 级日志 + 持久化 |
| 布局系统 | `components/layout/` | 侧边栏、导航、头部 |
| Agent 组件 | `components/agent/` | 任务创建、模式选择 |
| 报告导出 | `components/reports/` | PDF/Markdown 导出 |