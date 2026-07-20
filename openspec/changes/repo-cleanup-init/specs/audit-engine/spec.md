# Audit Engine Spec Delta — repo-cleanup-init

## REMOVED Requirement: `安全控制矩阵持久化 (Security Controls Persistence)`

**Reason**: 09_add_security_controls 迁移引入的 5 张表 (`security_controls`、`sensitive_operations`、`operation_required_controls`、`language_adapters`、`coverage_tracks`) 在自 3.1 版本合入以来 **从未被任何服务、API、Agent 逻辑读写**。相关加载器 `SecurityControlsConfigLoader` 唯一存在处即定义处，无消费者；YAML 数据目录 (`services/controls/data/`) 从未创建。 该能力属于 code-audit-main Fusion (v3.1) 融合过程中未落地的规划，实际覆盖率现由 `services/agent/core/coverage.py` (D1-D10) 承担，与 DB 表无关。

现有 Scenarios（如覆盖率跟踪）不受影响：`services/agent/core/coverage.py` 完全在内存 + `AgentTask.metadata` 里实现 D1-D10 十维度矩阵，独立于已删除的 `coverage_tracks` 表。

### Migration path

- 追加迁移 `023_drop_dead_tables.py` 显式 drop 上述 5 张表
- `alembic upgrade head` 从 022 → 023
- 不需要数据迁移（表始终为空）

## REMOVED Requirement: `Kunlun-M 静态代码分析工具集成`

**Reason**: `services/agent/tools/kunlun_tool.py` 中定义的 `KunlunMTool`/`KunlunRuleListTool`/`KunlunPluginTool` 三个工具类，**从未在 `_build_tools()` 中实例化**（`api/v1/endpoints/agent_tasks.py:1209-1241` 只实例化 Semgrep/Bandit/Gitleaks/NpmAudit/Safety/TruffleHog/OSVScanner）。 Kunlun-M 依赖目录 `Kunlun-M-master/` 从未在部署包中出现。 相关提示词 (`prompts/system_prompts.py`) 与 Agent 描述（`analysis.py`、`recon.py`）会让 LLM 尝试调用 `kunlun_scan` 工具但工具字典中不存在，反而是运行时噪声。

### Migration path

- 从工具字典、prompts、AGENTS.md 移除 `kunlun_scan` 相关描述
- 保留 Semgrep/Bandit/Gitleaks/npm audit/Safety/OSV/TruffleHog 作为唯一外部工具集

## MODIFIED Requirement: `外部安全工具矩阵`

从以下 8 个工具收敛为 7 个：

| 保留 | 判据 |
|------|------|
| `semgrep_scan` (SemgrepTool) | 在 `_build_tools()` 中实例化，Orchestrator 预扫也使用 |
| `bandit_scan` (BanditTool) | 在 `_build_tools()` 中实例化 |
| `gitleaks_scan` (GitleaksTool) | 同上 |
| `npm_audit` (NpmAuditTool) | 同上 |
| `safety_scan` (SafetyTool) | 同上 |
| `trufflehog_scan` (TruffleHogTool) | 同上 |
| `osv_scanner` (OSVScannerTool) | 同上 |
| ~~`kunlun_scan`~~ | 移除 |

## MODIFIED Requirement: `LLM 工具适配层`

`AgentTool.get_langchain_tool()` 从 `services/agent/tools/base.py` 移除。该方法：

- 定义于 base.py:135
- 全仓无任何调用方
- 依赖 `langchain.tools.Tool/StructuredTool`

移除后 `langchain`/`langchain-community`/`langchain-openai`/`langgraph` 全部可从 `pyproject.toml` 卸载。若未来引入 LangChain 生态，应重新评估是否使用 `LiteLLM` 已有的抽象层，避免重复引入。
