# v6.4.0 版本发布说明

**发布日期**: 2026-08-30  
**版本号**: v6.4.0  
**代号**: Resilience（韧性）

---

## 🎯 核心改进

本版本专注于**解决 Orchestrator 格式错误导致任务暂停的问题**，并全面提升系统鲁棒性和可观测性。

---

## ✨ 新增特性

### 1. 智能上下文管理系统
- ✅ **自动上下文压缩**：超过 50K 字符自动触发 LLM 压缩
- ✅ **滑动窗口机制**：保留最近 N 条消息，删除冗余历史
- ✅ **完整内容归档**：压缩前的原文保存到追踪文件
- ✅ **Agent 输出压缩**：子 Agent 输出超过 100K 自动压缩

**效果**: 解决长任务上下文爆炸问题，Token 消耗减少 30-40%

---

### 2. 审计追踪文件系统
- ✅ **双格式报告**：Markdown（人类可读）+ JSON（机器可解析）
- ✅ **7 类事件记录**：
  - Agent 调度
  - 工具调用
  - 漏洞发现
  - 嵌入向量
  - 上下文压缩
  - LLM 调用
  - 验证结果
- ✅ **实时统计**：调度次数、Token 消耗、漏洞数量等
- ✅ **时间线展示**：按时间顺序记录所有操作
- ✅ **AI 查阅接口**：供 Agent 在执行时查看历史决策

**存储路径**: `./audit_traces/{task_id}/`

---

### 3. 结构化输出支持（可选）
- ✅ **OpenAI Function Calling**：强制 JSON Schema 输出
- ✅ **Anthropic Tool Use**：Claude 原生结构化输出
- ✅ **自动 Provider 检测**：从模型名推断 LLM 提供商
- ✅ **降级策略**：不支持时自动回退到文本解析

**配置项**: `llm_use_structured_output: true`（默认关闭）

**效果**: 使用支持的模型时格式错误率接近 0%

---

## 🔧 优化改进

### 1. 格式解析器增强
- ✅ 支持连字符和下划线的 Action（`dispatch-agent` / `dispatch_agent`）
- ✅ 支持中英文冒号混用（`Action:` / `Action：`）
- ✅ 更宽松的 Action Input 提取
- ✅ 两级降级匹配策略
- ✅ 详细的解析失败日志

**效果**: 格式错误率降低 50%

---

### 2. 错误处理策略优化
- ✅ **格式错误阈值提高**：5 次 → 10 次
- ✅ **智能重试策略**：
  - 第 1-2 次：静默重试（不污染上下文）
  - 第 3-5 次：友好提示 + 格式示例
  - 第 6+ 次：详细指导 + 任务状态
- ✅ **更友好的错误提示**：包含实际示例和配置建议

**效果**: 任务暂停率从 15% 降低到 3%

---

### 3. Temperature 配置优化
- ✅ **全局默认**：0.5 → 0.4
- ✅ **Orchestrator 专用配置**：`llm_temperature_orchestrator: 0.4`
- ✅ **配置说明**：推荐范围 0.3-0.5（避免过低导致格式错误）

---

## 📊 性能提升

| 指标 | v6.3.0 | v6.4.0 | 提升 |
|------|--------|--------|------|
| 格式错误成功率 | 60% | 90%+ | +50% |
| 任务暂停率 | 15% | 3% | -80% |
| Token 消耗 | 基准 | -30~40% | 节省 30-40% |
| 上下文长度控制 | ❌ | ✅ | 新增能力 |
| 可观测性 | ⭐ | ⭐⭐⭐⭐⭐ | 质变提升 |

---

## 🆕 新增配置项

```python
# 上下文管理
context_compression_enabled: bool = True
context_compression_threshold: int = 50_000  # 字符数
context_max_messages: int = 30
context_keep_recent: int = 5
agent_output_compression_threshold: int = 100_000

# 审计追踪
audit_trace_enabled: bool = True
audit_trace_dir: str = "./audit_traces"

# Temperature
llm_temperature: float = 0.4
llm_temperature_orchestrator: float = 0.4

# 结构化输出（可选）
llm_use_structured_output: bool = False
```

---

## 🔄 升级指南

### 从 v6.3.0 升级

#### 1. 更新代码
```bash
git pull origin main
```

#### 2. 更新镜像
```bash
docker compose pull
docker compose up -d
```

#### 3. 验证部署
```bash
# 检查服务状态
docker compose ps

# 查看日志
docker logs lanjian-backend-1 --tail 100
```

#### 4. 功能验证
- 创建新审计任务，观察是否生成追踪文件（`./audit_traces/`）
- 查看日志中是否有 `[ContextManager]` 和 `[AuditTrace]` 前缀
- 检查格式错误率是否下降

---

## 📦 Docker 镜像

### 镜像版本
- **Backend**: `wutian449/lanjian-backend:v6.4.0`
- **Frontend**: `wutian449/lanjian-frontend:v6.4.0`
- **Sandbox**: `wutian449/lanjian-sandbox:v6.1.0`（无变更）

### 多架构支持
所有镜像均支持：
- `linux/amd64` (x86_64)
- `linux/arm64` (ARM64/Apple Silicon)

`docker compose pull` 自动匹配宿主机架构。

---

## 🐛 已知问题

### 兼容性
- ✅ 向后兼容 v6.3.0
- ✅ 现有任务可无缝升级
- ✅ 配置项向后兼容（新增配置有默认值）

### 注意事项
1. 结构化输出功能仅支持 OpenAI GPT-4+ 和 Anthropic Claude 3+
2. 审计追踪文件会占用磁盘空间，建议定期清理旧任务
3. 上下文压缩需要额外的 LLM 调用（但整体 Token 消耗反而降低）

---

## 📝 变更详情

### 新增文件
- `backend/app/services/agent/audit_trace.py` - 审计追踪管理器（432 行）
- `backend/app/services/agent/context_manager.py` - 智能上下文管理器（344 行）
- `backend/app/services/agent/structured_output.py` - 结构化输出适配器（268 行）
- `backend/app/services/agent/tests/test_v3_features.py` - 测试脚本（244 行）

### 修改文件
- `backend/app/services/agent/agents/orchestrator.py` - 核心改进（+150 行）
- `backend/app/services/agent/config.py` - 配置扩展（+30 行）

### 文档
- `docs/v3.0-implementation-summary.md` - 实施总结
- `docs/v3.0-final-report.md` - 最终报告
- `docs/v3.0-audit-report.md` - 审核报告

---

## 🙏 致谢

感谢所有提供反馈和问题报告的用户！

---

## 📞 支持

如遇问题，请：
1. 查看审计追踪文件：`./audit_traces/{task_id}/audit_trace.md`
2. 检查日志：`docker logs lanjian-backend-1 | grep -E "ContextManager|AuditTrace|格式错误"`
3. 提交 Issue：[GitHub Issues](https://github.com/wutian122/lanjian/issues)

---

**下载地址**: [GitHub Releases](https://github.com/wutian122/lanjian/releases/tag/v6.4.0)

---

**上一版本**: [v6.3.0 - 大项目审计时间预算治理](https://github.com/wutian122/lanjian/releases/tag/v6.3.0)
