# LLM 服务层 — 工厂 + 适配器 + 编排

12 文件、~3,028 行、11 个 LLM 提供商。LiteLLM 统一适配（8/11）+ 3 个原生适配器。
三层架构：LLMService（编排）→ LLMFactory（路由+缓存）→ BaseLLMAdapter（调用）。

## 目录结构

```
llm/
├── __init__.py              # 导出 LLMService + 类型 + PromptCacheManager + MemoryCompressor
├── types.py                 # LLMProvider 枚举(11) + dataclass 契约（103 行）
├── base_adapter.py          # Abstract BaseLLMAdapter + 重试/超时/错误分类（166 行）
├── factory.py               # LLMFactory（带实例缓存）+ 路由逻辑（194 行）
├── service.py               # LLMService 编排层（988 行）
├── prompt_cache.py          # Claude Prompt Caching（316 行）
├── memory_compressor.py     # 对话记忆压缩（333 行）
└── adapters/
    ├── __init__.py
    ├── litellm_adapter.py   # 主适配器（8/11 提供商，541 行）
    ├── baidu_adapter.py     # 百度文心 OAuth（147 行）
    ├── doubao_adapter.py    # 字节豆包（84 行）
    └── minimax_adapter.py   # MiniMax 特殊错误格式（89 行）
```

## 11 提供商路由表

| 提供商 | 适配器 | LiteLLM 前缀 | 备注 |
|--------|--------|-------------|------|
| OpenAI | LiteLLMAdapter | `openai/` | 自定义 base_url → 原生 OpenAI 客户端（讯飞 MaaS 兼容） |
| Claude | LiteLLMAdapter | `anthropic/` | 支持 Prompt Caching |
| Gemini | LiteLLMAdapter | `gemini/` | |
| DeepSeek | LiteLLMAdapter | `deepseek/` | 自定义 base_url |
| Qwen | LiteLLMAdapter | `openai/`（兼容模式） | 自定义 base_url |
| Zhipu | LiteLLMAdapter | `openai/`（兼容模式） | 自定义 base_url |
| Moonshot | LiteLLMAdapter | `openai/`（兼容模式） | 自定义 base_url |
| Ollama | LiteLLMAdapter | `ollama/` | |
| Baidu | **BaiduAdapter**（原生） | — | OAuth token 认证，绕过 LiteLLM |
| MiniMax | **MinimaxAdapter**（原生） | — | 特殊错误格式 |
| Doubao | **DoubaoAdapter**（原生） | — | 字节豆包 |

## 适配器链（请求流程）

```
Agent BaseAgent.llm_service.chat_completion(messages, tools)
    │
    ▼
LLMService.config                 → 配置优先级链解析（用户配置 > env > 默认）
    │  转换 dict → LLMMessage，构建 LLMRequest
    ▼
LLMFactory.create_adapter(config) → 带缓存（key = provider:model:api_key[:8]）
    │  路由：{BAIDU,MINIMAX,DOUBAO} → 原生 / 其余 → LiteLLM
    ▼
adapter.complete(LLMRequest)      → LiteLLM 重试 5 次（延迟 2s）
    │  Claude → prompt_cache_manager 添加缓存断点
    │  OPENAI+自定义base_url → _native_openai_call()（讯飞 MaaS）
    │  else → litellm.acompletion()
    ▼
LLM Provider API → LLMResponse → 返回 Agent
```

## 配置优先级链（LLMService.config）

| 配置项 | 优先级 1 | 优先级 2 | 优先级 3 |
|--------|---------|---------|---------|
| Provider | `user_config` (DB) | env `LLM_PROVIDER` | `'openai'` |
| API Key | `user_config` | env `LLM_API_KEY` | env `{PROVIDER}_API_KEY` |
| Base URL | `user_config` | env `LLM_BASE_URL` | `DEFAULT_BASE_URLS` |
| Model | `user_config` | env `LLM_MODEL` | `DEFAULT_MODELS` |
| Timeout | `user_config` (ms→s) | env `LLM_TIMEOUT` (s) | `150s` |

## 三大对外接口（LLMService）

| 接口 | 用途 | 返回 |
|------|------|------|
| `chat_completion(messages, tools)` | Agent 主接口（支持工具调用） | `{content, usage, tool_calls}` |
| `chat_completion_raw(messages)` | 简化版（无工具） | `{content, usage}` |
| `chat_completion_stream(messages)` | 流式 async generator | 逐 chunk yield |

## 7 策略 JSON 修复管道（`_parse_json`）

1. 直接 `json.loads`
2. 清理 → 修复格式
3. 从 markdown 代码块提取
4. 智能 JSON 对象提取
5. 截断 JSON 修复
6. 激进修复
7. `json-repair` 库兜底

## 消费者

| 消费者 | 调用方式 |
|--------|---------|
| `BaseAgent` (agents/base.py) | `llm_service.chat_completion()` / `chat_completion_stream()` |
| `scanner.py` | `LLMService(user_config)` |
| Agent 核心基础设施 | circuit_breaker + rate_limiter 包裹 LLM 调用 |
| 模块单例 | `llm_service = LLMService()` (service.py:1064) |

## 关键配置常量

| 常量 | 位置 | 值 |
|------|------|-----|
| `NATIVE_ONLY_PROVIDERS` | factory.py:20 | `{BAIDU, MINIMAX, DOUBAO}` |
| LiteLLM 重试 | litellm_adapter | max_attempts=5, delay=2.0s |
| 基类重试 | base_adapter | max_attempts=3, delay=1.0s，4xx 不重试 |
| 默认超时 | types.py | 150s |
| CacheStrategy | prompt_cache.py | SYSTEM_ONLY / SYSTEM_AND_EARLY / MULTI_POINT / NONE |
| 压缩阈值 | memory_compressor.py | token > 90% 时触发 |

## 新增提供商 Checklist

1. 在 `types.py` 的 `LLMProvider` 枚举添加
2. 在 `DEFAULT_MODELS` / `DEFAULT_BASE_URLS` 添加默认值
3. **LiteLLM 兼容** → 在 `litellm_adapter.py` 的 `PROVIDER_PREFIX_MAP` 添加映射
4. **API 格式特殊** → 新建 `adapters/{name}_adapter.py` 继承 `BaseLLMAdapter`，并在 `factory.py` 的 `NATIVE_ONLY_PROVIDERS` + `_create_native_adapter` 注册
5. 在 `factory.py` 的 `get_available_models` 添加模型列表
6. 如需环境变量：在 `service.py` 的 `_get_provider_api_key()` / `_get_provider_base_url()` 添加

## 反模式

- 禁止绕过 LLMFactory 直接实例化适配器
- 禁止在 Agent 中硬编码 provider/model
- 禁止跳过 JSON 修复管道（所有 LLM 输出必须经 `_parse_json`）
- 讯飞 MaaS → 必须设置 `OPENAI_API_BASE`，否则会走 LiteLLM 非原生路径
