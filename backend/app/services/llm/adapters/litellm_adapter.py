"""
LiteLLM 统一适配器
支持通过 LiteLLM 调用多个 LLM 提供商，使用统一的 OpenAI 兼容格式

增强功能:
- Prompt Caching: 为支持的 LLM（如 Claude）添加缓存标记
- 智能重试: 指数退避重试策略
- 流式输出: 支持逐 token 返回
"""

import logging
import re
from typing import Dict, Any, Optional, List
from ..base_adapter import BaseLLMAdapter
from ..types import (
    LLMConfig,
    LLMRequest,
    LLMResponse,
    LLMUsage,
    LLMProvider,
    LLMError,
    DEFAULT_BASE_URLS,
)
from ..prompt_cache import prompt_cache_manager, estimate_tokens

logger = logging.getLogger(__name__)


# 🔥 LLM 错误分类关键词（非流式 + 流式共用，避免两处不一致）
# 注意：LiteLLM 会把上游 401(authorization failed) 包装成 RateLimitError 抛出，
# 且 RateLimitError.status_code 硬编码为 429，无法用 status_code 区分，
# 只能靠字符串匹配。关键词须避免裸数字（如 "401" 会误匹配 request_id/retry 秒数）
# 和过宽词（如 "exceeded" 会把标准 429 "rate limit exceeded" 误判为配额用尽）。
_QUOTA_KEYWORDS = (
    ["余额不足", "资源包", "充值", "quota", "insufficient", "balance", "billing"]
)
_AUTH_KEYWORDS = (
    ["authorization", "authentication", "unauthorized", "invalid api key"]
)

# 🔥 max_tokens 超限错误检测：服务商返回的 400 含明确限制值，提取后给用户友好提示
# 不 clamp max_tokens（各模型限制差异大），仅解析错误自适应提示
_MAX_TOKENS_LIMIT_PATTERN = re.compile(
    r"max_tokens.*?(?:less|smaller|<=|at most|up to|exceed)[^0-9]*(\d+)",
    re.IGNORECASE,
)


def _detect_max_tokens_error(error_str: str):
    """检测 max_tokens 超限错误。

    返回 (limit_value, user_message) 或 None。limit_value 来自服务商错误消息，
    自适应任何模型的限制，无需维护映射表。
    """
    m = _MAX_TOKENS_LIMIT_PATTERN.search(error_str)
    if m:
        limit = m.group(1)
        return (limit, f"max_tokens 超过当前模型上限 {limit}，请在系统配置页调小 llmMaxTokens")
    return None


# ---------------------------------------------------------------------------
# 关思考护栏（structured-output-protocol / spec: thinking-stream-separation）
#
# 老板 2026-09-03 实测（Qwen3 thinking 模型，SGLang --reasoning-parser qwen3）：
# 服务端 reasoning-parser 修正前，任何"关闭思考"的手段都会让模型正文被服务端
# parser 整个吞进 reasoning_content、content 恒为空——比思考退化乱码更糟：
#   * enable_thinking: false 请求参数被端点忽略（无害但无效）；
#   * 提示词注入 <|think_off|>：思考真的关了，但 content 恒为空（有害）；
#   * /no_think 是 Qwen3 官方软切换命令（/think 为开启），同属关闭方向。
# 护栏在请求构造的最后出口（_send_request / stream_complete / _native_openai_call
# 三处出站路径）剥除关思考参数与提示词标记，请求强制以思考模式发出。
# 作用域（误伤实测后收窄）：enable_thinking 参数与 <|think_off|> token 全域剥除；
# /no_think 仅剥 system/user 消息 content 中的独立命令形态（词边界），
# assistant/tool 消息（模型输出/审计证据）与 tools 描述中的同形字符串不动。
#
# 解除条件：SGLang/vLLM 服务端 reasoning-parser 修正（关思考后 content 能正常
# 返回）并经实测验证后，本护栏与其测试可一并移除。在此之前配置层（core/config.py、
# 用户配置、前端设置页）SHALL NOT 新增任何关思考开关项。
# ---------------------------------------------------------------------------

# 关思考提示词标记分两类（第 1 轮修复按误伤实测收窄作用域）：
# 1. <|think_off|>：special token 语法，业务内容（代码/URL/observation/工具描述）
#    不可能自然出现——全域字符串清洗，含 assistant/tool 消息与 tools 描述，
#    防注入把 token 藏进审计内容；
# 2. /no_think：Qwen3 官方软切换命令（/think 为开启），是自然语言可出现的字符序列
#    （代码常量 /no_thinking_allowed、URL 路径段 .../no_think/docs、tool observation、
#    工具描述都曾实测误伤）——仅清洗 system/user 角色消息 content 中的独立命令形态
#    （前后词边界：邻接字母/数字/下划线/路径斜杠时不匹配）。assistant/tool 消息是
#    模型输出与审计证据，一个字都不许动。大小写不敏感（服务端模板匹配同样宽容）。
_THINK_OFF_TOKEN_PATTERN = re.compile(r"<\|think_off\|>", re.IGNORECASE)
_NO_THINK_COMMAND_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_/])/no_think(?![A-Za-z0-9_/])", re.IGNORECASE
)

# /no_think 软开关仅在这两类角色的消息 content 内清洗（调用侧自身构造的提示词）
_NO_THINK_COMMAND_ROLES = frozenset({"system", "user"})

# 思考开关键名（SGLang/vLLM/Qwen dashscope 三家统一为 enable_thinking），
# 可出现在请求顶层、extra_body/extra_params、chat_template_kwargs 任意层级。
_THINK_SWITCH_KEY = "enable_thinking"

# enable_thinking 的"关"语义字符串值（bool False / int 0 单独判定）
_THINK_OFF_STR_VALUES = frozenset({"false", "0", "no", "off", "disable", "disabled"})


def _is_thinking_off_value(value: Any) -> bool:
    """判定开关值是否为"关思考"语义。只拦关闭方向：True/1/"true"/"yes" 不拦。"""
    if isinstance(value, bool):
        return value is False
    if isinstance(value, int):
        return value == 0
    if isinstance(value, str):
        return value.strip().lower() in _THINK_OFF_STR_VALUES
    return False


def _clean_text_node(text: str, path: str, hits: List[str], *, allow_no_think: bool) -> str:
    """清洗字符串节点。

    - <|think_off|>：全域清洗（allow_no_think 无关）；
    - /no_think：仅 allow_no_think=True（system/user 消息 content 子树）时清洗，
      且须为独立命令形态（词边界正则排除代码常量/URL 路径误伤）。
    """
    result = text
    token_hits = _THINK_OFF_TOKEN_PATTERN.findall(result)
    if token_hits:
        hits.append(f"{path} 含关思考标记 {sorted(set(token_hits))}（special token 已剥除，正文保留）")
        result = _THINK_OFF_TOKEN_PATTERN.sub("", result)
    if allow_no_think:
        command_hits = _NO_THINK_COMMAND_PATTERN.findall(result)
        if command_hits:
            hits.append(f"{path} 含软关闭命令 /no_think（命令已剥除，正文保留）")
            result = _NO_THINK_COMMAND_PATTERN.sub("", result)
    return result


def _strip_thinking_off(node: Any, path: str, hits: List[str], *, allow_no_think: bool = False) -> Any:
    """递归剥除 dict/list/str 节点中的关思考参数与提示词标记（原地修改容器）。

    - dict：键名（大小写不敏感）为 enable_thinking 且值为关语义 → 删键并记录；
      形如 chat message 的 dict（同时含 role 与 content 键）按 role 决定 content
      子树是否允许清洗 /no_think（仅 system/user）；其余值递归；
    - list：逐元素递归（messages 列表、多模态 content parts），allow_no_think 透传；
    - str：按 _clean_text_node 规则清洗。

    allow_no_think 仅在 system/user 消息的 content 子树内为 True；tools 描述、
    extra_body、assistant/tool 消息等位置恒为 False（<|think_off|> 不受此限）。
    """
    if isinstance(node, dict):
        role = node.get("role")
        is_chat_message = isinstance(role, str) and "content" in node
        content_allows_no_think = allow_no_think or (
            is_chat_message and role.lower() in _NO_THINK_COMMAND_ROLES
        )
        for key in list(node.keys()):
            child_path = f"{path}.{key}" if path else str(key)
            if (
                isinstance(key, str)
                and key.lower() == _THINK_SWITCH_KEY
                and _is_thinking_off_value(node[key])
            ):
                hits.append(f"{child_path}={node[key]!r}（关思考参数已剥除）")
                del node[key]
            elif is_chat_message and key == "content":
                node[key] = _strip_thinking_off(
                    node[key], child_path, hits, allow_no_think=content_allows_no_think
                )
            else:
                node[key] = _strip_thinking_off(
                    node[key], child_path, hits, allow_no_think=allow_no_think
                )
        return node
    if isinstance(node, list):
        for idx, item in enumerate(node):
            node[idx] = _strip_thinking_off(
                item, f"{path}[{idx}]", hits, allow_no_think=allow_no_think
            )
        return node
    if isinstance(node, str):
        return _clean_text_node(node, path, hits, allow_no_think=allow_no_think)
    return node


def _assert_no_thinking_off(params: Dict[str, Any], *, source: str) -> Dict[str, Any]:
    """关思考护栏：在请求构造的最后出口对即将出站的 kwargs 调用，原地剥除一切
    关思考参数/提示词标记，保证请求以思考模式发出。

    背景：Qwen3 thinking 经 SGLang（--reasoning-parser qwen3）服务端在 parser
    修正前，关思考会导致正文被吞进 reasoning_content、content 恒空（老板实测，
    见本模块护栏注释）。拦截对象（大小写不敏感、递归检查）：
      1. 顶层或 extra_body/extra_params/chat_template_kwargs 任意层级的
         enable_thinking=关语义（False/0/"false"/"no"/"off"/"disable(d)"）；
      2. <|think_off|> special token：全域字符串清洗（业务内容不可能自然出现，
         含 assistant/tool 消息与 tools 描述，防注入藏入审计内容）；
      3. /no_think 软开关：仅清洗 system/user 角色消息 content 中的独立命令形态
         （词边界排除 /no_thinking_allowed、URL 路径段等误伤）；assistant/tool
         消息是模型输出/审计证据绝不改动，/think 开启标记不拦。

    命中处理：剥除该参数/标记 + logger.warning（含触发位置 source 与剥除明细）。
    适配器层拿不到 event_emitter（llm 适配层无事件链路引用），故以 logger.warning
    留痕；返回 {"stripped": [明细...]} 供调用方/未来事件链路发射 warning 事件。
    零命中时不修改 params、不输出日志（正常请求零行为变化）。

    解除条件：服务端 reasoning-parser 修正并实测验证后本护栏可移除。

    Args:
        params: 即将发往端点的请求 kwargs（litellm/acompletion 或 openai create 风格）。
        source: 触发位置标识（"_send_request" / "stream_complete" / "_native_openai_call"）。

    Returns:
        {"stripped": [剥除项描述, ...]}；空列表表示零命中。
    """
    hits: List[str] = []
    _strip_thinking_off(params, "", hits)
    if hits:
        logger.warning(
            "关思考护栏拦截（位置=%s）：检测到 %d 处关思考参数/标记，已剥除并强制以思考模式"
            "发出请求（服务端 reasoning-parser 修正前关思考会导致 content 恒空）。明细：%s",
            source,
            len(hits),
            "；".join(hits),
        )
    return {"stripped": hits}


class LiteLLMAdapter(BaseLLMAdapter):
    """
    LiteLLM 统一适配器
    
    支持的提供商:
    - OpenAI (openai/gpt-4o-mini)
    - Claude (anthropic/claude-3-5-sonnet-20241022)
    - Gemini (gemini/gemini-1.5-flash)
    - DeepSeek (deepseek/deepseek-chat)
    - Qwen (qwen/qwen-turbo) - 通过 OpenAI 兼容模式
    - Zhipu (zhipu/glm-4-flash) - 通过 OpenAI 兼容模式
    - Moonshot (moonshot/moonshot-v1-8k) - 通过 OpenAI 兼容模式
    - Ollama (ollama/llama3)
    """

    # LiteLLM 模型前缀映射
    PROVIDER_PREFIX_MAP = {
        LLMProvider.OPENAI: "openai",
        LLMProvider.CLAUDE: "anthropic",
        LLMProvider.GEMINI: "gemini",
        LLMProvider.DEEPSEEK: "deepseek",
        LLMProvider.QWEN: "openai",  # 使用 OpenAI 兼容模式
        LLMProvider.ZHIPU: "openai",  # 使用 OpenAI 兼容模式
        LLMProvider.MOONSHOT: "openai",  # 使用 OpenAI 兼容模式
        LLMProvider.OLLAMA: "ollama",
    }

    # 需要自定义 base_url 的提供商
    CUSTOM_BASE_URL_PROVIDERS = {
        LLMProvider.QWEN,
        LLMProvider.ZHIPU,
        LLMProvider.MOONSHOT,
        LLMProvider.DEEPSEEK,
    }

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        self._litellm_model = self._get_litellm_model()
        self._api_base = self._get_api_base()

    def _get_litellm_model(self) -> str:
        """获取 LiteLLM 格式的模型名称
        
        对于使用第三方 OpenAI 兼容 API（如 SiliconFlow）的情况：
        - 如果用户设置了自定义 base_url，且模型名包含 / (如 Qwen/Qwen3-8B)
        - 需要将其转换为 openai/Qwen/Qwen3-8B 格式
        - 因为 LiteLLM 只认识 openai 作为有效前缀
        """
        provider = self.config.provider
        model = self.config.model

        # 检查模型名是否已经包含前缀
        if "/" in model:
            # 提取第一部分作为可能的 provider 前缀
            prefix_part = model.split("/")[0].lower()
            
            # LiteLLM 认识的有效 provider 前缀列表
            valid_litellm_prefixes = [
                "openai", "anthropic", "gemini", "deepseek", "ollama",
                "azure", "huggingface", "together", "groq", "mistral",
                "anyscale", "replicate", "bedrock", "vertex_ai", "cohere",
                "sagemaker", "palm", "ai21", "nlp_cloud", "aleph_alpha",
                "petals", "baseten", "vllm", "cloudflare", "xinference"
            ]
            
            # 如果前缀是 LiteLLM 认识的，直接返回
            if prefix_part in valid_litellm_prefixes:
                return model
            
            # 如果用户设置了自定义 base_url，将其视为 OpenAI 兼容 API
            # 例如 SiliconFlow 使用模型名 "Qwen/Qwen3-8B"
            if self.config.base_url:
                logger.debug(f"使用自定义 base_url，将模型 {model} 视为 OpenAI 兼容格式")
                return f"openai/{model}"
            
            # 对于没有自定义 base_url 的情况，尝试使用 provider 的前缀
            prefix = self.PROVIDER_PREFIX_MAP.get(provider, "openai")
            return f"{prefix}/{model}"

        # 获取 provider 前缀
        prefix = self.PROVIDER_PREFIX_MAP.get(provider, "openai")
        
        return f"{prefix}/{model}"

    def _extract_api_response(self, error: Exception) -> Optional[str]:
        """从异常中提取 API 服务器返回的原始响应信息"""
        error_str = str(error)

        # 尝试提取 JSON 格式的错误信息
        import re
        import json

        # 匹配 {'error': {...}} 或 {"error": {...}} 格式
        json_pattern = r"\{['\"]error['\"]:\s*\{[^}]+\}\}"
        match = re.search(json_pattern, error_str)
        if match:
            try:
                # 将单引号替换为双引号以便 JSON 解析
                json_str = match.group().replace("'", '"')
                error_obj = json.loads(json_str)
                if 'error' in error_obj:
                    err = error_obj['error']
                    code = err.get('code', '')
                    message = err.get('message', '')
                    return f"[{code}] {message}" if code else message
            except (AttributeError, KeyError, TypeError, ValueError):
                # L2: 解析 error dict 常见的四类；其他异常上抛
                pass

        # 尝试提取 message 字段
        message_pattern = r"['\"]message['\"]:\s*['\"]([^'\"]+)['\"]"
        match = re.search(message_pattern, error_str)
        if match:
            return match.group(1)

        # 尝试从 litellm 异常中获取原始消息
        if hasattr(error, 'message'):
            return error.message
        if hasattr(error, 'llm_provider'):
            # litellm 异常通常包含原始错误信息
            return error_str.split(' - ')[-1] if ' - ' in error_str else None

        return None

    @staticmethod
    def _detect_xunfei_error(error_str: str) -> Optional[str]:
        """检测讯飞 MaaS (one-api) 特定错误并返回友好提示

        Args:
            error_str: API 返回的原始错误字符串

        Returns:
            检测到特定错误时返回友好提示，否则返回 None
        """
        if not isinstance(error_str, str):
            return None

        # 检测 one-api 网关转发的讯飞星火后端认证故障
        keywords = [
            "one_api_error",
            "xunfei_request_failed",
            "Dial authUrl",
            "讯飞",
        ]
        if any(k in error_str for k in keywords):
            return (
                "讯飞星辰MaaS平台后端服务暂时不可用（服务商侧故障）。\n"
                "建议操作：\n"
                "1. 等待 1-2 分钟后重新点击[测试连接]\n"
                "2. 如持续失败，登录讯飞星辰控制台检查 API Key 状态\n"
                "3. 考虑切换其他 LLM 提供商作为备用"
            )
        return None

    def _get_api_base(self) -> Optional[str]:
        """获取 API 基础 URL"""
        # 优先使用用户配置的 base_url
        if self.config.base_url:
            return self.config.base_url

        # 对于需要自定义 base_url 的提供商，使用默认值
        if self.config.provider in self.CUSTOM_BASE_URL_PROVIDERS:
            return DEFAULT_BASE_URLS.get(self.config.provider)

        # Ollama 使用本地地址
        if self.config.provider == LLMProvider.OLLAMA:
            return DEFAULT_BASE_URLS.get(LLMProvider.OLLAMA, "http://localhost:11434")

        return None

    async def _native_openai_call(self, **kwargs: Any):
        """使用原生 OpenAI 客户端直接调用（绕过 LiteLLM）

        适用于设置了自定义 base_url 的 OpenAI 兼容 API（如讯飞 MaaS）。
        """
        # 关思考护栏（structured-output-protocol）：native 路径最后出口。
        # 与 _send_request 出口那道幂等双保险——未来若有新调用方直接调本方法也绕不过。
        _assert_no_thinking_off(kwargs, source="_native_openai_call")

        import openai

        # 提取模型名（去掉 openai/ 前缀）
        model = kwargs.get("model", "")
        if "/" in model:
            model = model.split("/", 1)[1]

        client = openai.AsyncOpenAI(
            api_key=kwargs.get("api_key", ""),
            base_url=kwargs.get("api_base", ""),
            timeout=kwargs.get("timeout", 150),
        )

        messages = kwargs.get("messages", [])
        params: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if "temperature" in kwargs:
            params["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            params["max_tokens"] = kwargs["max_tokens"]

        # 结构化输出协议（structured-output-protocol）：tools/response_format 为
        # OpenAI 标准参数；extra_body（repetition_penalty 等 provider 特有参数）
        # 必须原样透传给 create()——openai SDK 的官方透传机制会把 extra_body 合并
        # 进 HTTP body 顶层（SGLang 收到的请求体语义不变）。
        # 禁止 params.update(extra_body) 展开：create() 无 **kwargs 也无
        # repetition_penalty 形参，展开必 TypeError（openai 2.12.0 实证）。
        if kwargs.get("tools"):
            params["tools"] = kwargs["tools"]
        if kwargs.get("response_format"):
            params["response_format"] = kwargs["response_format"]
        extra_body = kwargs.get("extra_body")
        if extra_body:
            params["extra_body"] = extra_body

        return await client.chat.completions.create(**params)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """使用 LiteLLM 发送请求"""
        try:
            await self.validate_config()
            # 增加重试次数以应对第三方 API（如讯飞 MaaS）的间歇性故障
            return await self.retry(lambda: self._send_request(request), max_attempts=5, delay=2.0)
        except Exception as error:
            self.handle_error(error, f"LiteLLM ({self.config.provider.value}) API调用失败")

    async def _send_request(self, request: LLMRequest) -> LLMResponse:
        """发送请求到 LiteLLM"""
        import litellm
        import openai
        
        # 启用 LiteLLM 调试模式以获取更详细的错误信息
        # 注释掉下一行可关闭调试模式
        # litellm._turn_on_debug()
        
        # 禁用 LiteLLM 的缓存，确保每次都实际调用 API
        litellm.cache = None
        
        # 禁用 LiteLLM 自动添加的 reasoning_effort 参数
        # 这可以防止模型名称被错误解析为 effort 参数
        litellm.drop_params = True
        
        # 构建消息
        messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]
        
        # 🔥 Prompt Caching: 为支持的 LLM 添加缓存标记
        cache_enabled = False
        if self.config.provider == LLMProvider.CLAUDE:
            # 估算系统提示词 token 数
            system_tokens = 0
            for msg in messages:
                if msg.get("role") == "system":
                    system_tokens += estimate_tokens(msg.get("content", ""))
            
            messages, cache_enabled = prompt_cache_manager.process_messages(
                messages=messages,
                model=self.config.model,
                provider=self.config.provider.value,
                system_prompt_tokens=system_tokens,
            )
            
            if cache_enabled:
                logger.debug(f"🔥 Prompt Caching enabled for {self.config.model}")

        # 构建请求参数
        kwargs: Dict[str, Any] = {
            "model": self._litellm_model,
            "messages": messages,
            "temperature": request.temperature if request.temperature is not None else self.config.temperature,
            "max_tokens": request.max_tokens if request.max_tokens is not None else self.config.max_tokens,
            "top_p": request.top_p if request.top_p is not None else self.config.top_p,
        }

        # 结构化输出协议（structured-output-protocol）：
        # tools/response_format 是 OpenAI 标准参数（openai/ 前缀下 drop_params 不丢）；
        # extra_params（repetition_penalty 等 provider 特有参数）经 litellm extra_body
        # 官方透传机制合并进 HTTP body（litellm custom_httpx: data = {**data, **extra_body}），
        # 不受 drop_params 影响。native 路径（_native_openai_call）同样原样透传 extra_body，
        # 由 openai SDK 合并进 body——两条路径对端点呈现的请求体一致。
        if request.tools:
            kwargs["tools"] = request.tools
        if request.response_format:
            kwargs["response_format"] = request.response_format
        if request.extra_params:
            kwargs["extra_body"] = request.extra_params

        # 设置 API Key
        if self.config.api_key and self.config.api_key != "ollama":
            kwargs["api_key"] = self.config.api_key

        # 设置 API Base URL
        if self._api_base:
            kwargs["api_base"] = self._api_base
            logger.debug(f"🔗 使用自定义 API Base: {self._api_base}")

        # 设置超时
        kwargs["timeout"] = self.config.timeout

        # 对于 OpenAI 提供商，添加额外参数
        if self.config.provider == LLMProvider.OPENAI:
            kwargs["frequency_penalty"] = self.config.frequency_penalty
            kwargs["presence_penalty"] = self.config.presence_penalty

        # 关思考护栏（structured-output-protocol）：非流式路径最后出口。
        # kwargs 已完全成型，native/litellm 两个分支都在其后发出，统一在此清洗；
        # native 分支内 _native_openai_call 还有一道幂等双保险。
        _assert_no_thinking_off(kwargs, source="_send_request")

        try:
            # 当使用 OPENAI + 自定义 base_url 时，直接使用原生 OpenAI 客户端
            # LiteLLM 在 uvicorn 上下文中与某些第三方 API 存在兼容问题（如讯飞 MaaS）
            if (self.config.provider == LLMProvider.OPENAI
                    and self._api_base
                    and self.config.api_key):
                response = await self._native_openai_call(**kwargs)
            else:
                # 调用 LiteLLM
                response = await litellm.acompletion(**kwargs)
        except litellm.exceptions.AuthenticationError as e:
            api_response = self._extract_api_response(e)
            raise LLMError(f"API Key 无效或已过期", self.config.provider, 401, api_response=api_response)
        except litellm.exceptions.RateLimitError as e:
            error_msg = str(e)
            error_msg_lower = error_msg.lower()
            api_response = self._extract_api_response(e)
            # 余额不足 / 配额用尽
            if any(keyword in error_msg_lower for keyword in _QUOTA_KEYWORDS):
                raise LLMError(f"账户余额不足或配额已用尽，请充值后重试", self.config.provider, 402, api_response=api_response)
            # 🔥 认证失败：LiteLLM 会把上游 401（authorization failed）也包装成 RateLimitError 抛出，
            # 必须在此识别并归为 401，否则会误判为 429 限流导致 Orchestrator 走 30s×3 重试而非立即终止
            if any(keyword in error_msg_lower for keyword in _AUTH_KEYWORDS):
                raise LLMError(f"API Key 无效或已过期，请检查配置", self.config.provider, 401, api_response=api_response)
            raise LLMError(f"API 调用频率超限，请稍后重试", self.config.provider, 429, api_response=api_response)
        except litellm.exceptions.APIConnectionError as e:
            api_response = self._extract_api_response(e)
            raise LLMError(f"无法连接到 API 服务", self.config.provider, api_response=api_response)
        except litellm.exceptions.APIError as e:
            api_response = self._extract_api_response(e)
            error_str = str(e)
            # 🔥 max_tokens 超限：解析服务商返回的限制值，给友好提示
            mt = _detect_max_tokens_error(error_str)
            if mt:
                limit, msg = mt
                raise LLMError(msg, self.config.provider, 400, api_response=api_response)
            raise LLMError(f"API 错误", self.config.provider, getattr(e, 'status_code', None), api_response=api_response)
        except openai.AuthenticationError as e:
            raise LLMError(f"API Key 无效或已过期", self.config.provider, 401,
                           api_response=str(e))
        except openai.RateLimitError as e:
            raise LLMError(f"API 调用频率超限，请稍后重试", self.config.provider, 429,
                           api_response=str(e))
        except openai.APIConnectionError as e:
            raise LLMError(f"无法连接到 API 服务", self.config.provider,
                           api_response=str(e))
        except openai.APIStatusError as e:
            # 检测讯飞 MaaS (one-api) 特定错误，提供更精准的提示
            error_str = str(e)
            xunfei_msg = self._detect_xunfei_error(error_str)
            if xunfei_msg:
                raise LLMError(xunfei_msg, self.config.provider,
                               status_code=e.status_code, api_response=error_str)
            # 🔥 max_tokens 超限：解析服务商返回的限制值，给友好提示
            mt = _detect_max_tokens_error(error_str)
            if mt:
                limit, msg = mt
                raise LLMError(msg, self.config.provider, 400, api_response=error_str)
            raise LLMError(f"API 服务异常 ({e.status_code})", self.config.provider,
                           status_code=e.status_code, api_response=error_str)
        except Exception as e:
            # 捕获其他异常并重新抛出
            error_msg = str(e)
            api_response = self._extract_api_response(e)
            if "invalid_api_key" in error_msg.lower() or "incorrect api key" in error_msg.lower():
                raise LLMError(f"API Key 无效", self.config.provider, 401, api_response=api_response)
            elif "authentication" in error_msg.lower():
                raise LLMError(f"认证失败", self.config.provider, 401, api_response=api_response)
            elif any(keyword in error_msg for keyword in ["余额不足", "资源包", "充值", "quota", "insufficient", "balance"]):
                raise LLMError(f"账户余额不足或配额已用尽", self.config.provider, 402, api_response=api_response)
            raise

        # 解析响应
        if not response:
            raise LLMError("API 返回空响应", self.config.provider)
            
        choice = response.choices[0] if response.choices else None
        if not choice:
            raise LLMError("API响应格式异常: 缺少choices字段", self.config.provider)

        usage = None
        if hasattr(response, "usage") and response.usage:
            usage = LLMUsage(
                prompt_tokens=response.usage.prompt_tokens or 0,
                completion_tokens=response.usage.completion_tokens or 0,
                total_tokens=response.usage.total_tokens or 0,
            )
            
            # 🔥 更新 Prompt Cache 统计
            if cache_enabled and hasattr(response.usage, "cache_creation_input_tokens"):
                prompt_cache_manager.update_stats(
                    cache_creation_input_tokens=getattr(response.usage, "cache_creation_input_tokens", 0),
                    cache_read_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0),
                    total_input_tokens=response.usage.prompt_tokens or 0,
                )

        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            usage=usage,
            finish_reason=choice.finish_reason,
        )

    async def stream_complete(self, request: LLMRequest):
        """
        流式调用 LLM，逐 token 返回

        Yields:
            dict: token 块 {"type": "token", "kind": "content"|"reasoning",
                    "content": 增量文本, "accumulated": 思考+正文按序拼接（兼容键）,
                    "accumulated_content": 正文累计, "accumulated_reasoning": 思考累计}；
                  done 块 {"type": "done", "content": 正文累计, "reasoning": 思考累计,
                    "accumulated": 拼接累计, "usage": dict, "finish_reason": str}；
                  error 块 {"type": "error", ..., "accumulated": 拼接累计}
        """
        import litellm

        await self.validate_config()

        litellm.cache = None
        litellm.drop_params = True

        messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]

        # 🔥 估算输入 token 数量（用于在无法获取真实 usage 时进行估算）
        input_tokens_estimate = sum(estimate_tokens(msg["content"]) for msg in messages)

        kwargs = {
            "model": self._litellm_model,
            "messages": messages,
            "temperature": request.temperature if request.temperature is not None else self.config.temperature,
            "max_tokens": request.max_tokens if request.max_tokens is not None else self.config.max_tokens,
            "top_p": request.top_p if request.top_p is not None else self.config.top_p,
            "stream": True,  # 启用流式输出
        }

        # 结构化输出协议（structured-output-protocol）：与 _send_request 保持一致，
        # tools/response_format 为标准参数，extra_params 经 extra_body 透传
        if request.tools:
            kwargs["tools"] = request.tools
        if request.response_format:
            kwargs["response_format"] = request.response_format
        if request.extra_params:
            kwargs["extra_body"] = request.extra_params

        # 🔥 对于支持的模型，请求在流式输出中包含 usage 信息
        # OpenAI API 支持 stream_options
        if self.config.provider in [LLMProvider.OPENAI, LLMProvider.DEEPSEEK]:
            kwargs["stream_options"] = {"include_usage": True}

        if self.config.api_key and self.config.api_key != "ollama":
            kwargs["api_key"] = self.config.api_key

        if self._api_base:
            kwargs["api_base"] = self._api_base

        kwargs["timeout"] = self.config.timeout

        # 🔥 重复惩罚参数（全局生效，与 complete() 保持一致）
        if self.config.frequency_penalty:
            kwargs["frequency_penalty"] = self.config.frequency_penalty
        if self.config.presence_penalty:
            kwargs["presence_penalty"] = self.config.presence_penalty

        # 关思考护栏（structured-output-protocol）：流式路径最后出口，
        # kwargs 已完全成型，在 litellm.acompletion 发出前统一清洗
        _assert_no_thinking_off(kwargs, source="stream_complete")

        # structured-output-protocol Task 3：思考流/正文流在 chunk 层分离。
        # accumulated_content 仅累计 delta.content（正文），accumulated_reasoning
        # 仅累计 delta.reasoning_content/delta.thinking（思考）；accumulated_all
        # 按流到达顺序拼接两者，作为旧 "accumulated" 键的兼容值（Task 4 前
        # base.py 仍读旧键），也用于输出 token 估算（思考 token 同样计费）。
        accumulated_content = ""
        accumulated_reasoning = ""
        accumulated_all = ""
        # structured-output-protocol Task 7：流式 tool_calls 聚合。
        # OpenAI 流式形态：delta.tool_calls[{index, id, function:{name, arguments 增量}}]，
        # id/name 通常仅首块到达，arguments 按块增量拼接；按 index 归槽。
        aggregated_tool_calls: Dict[int, Dict[str, Any]] = {}
        finished = False  # 是否已发射 finish_reason 的 done（防兜底路径重复发 done）
        final_usage = None  # 🔥 存储最终的 usage 信息
        chunk_count = 0  # 🔥 跟踪 chunk 数量

        try:
            response = await litellm.acompletion(**kwargs)

            async for chunk in response:
                chunk_count += 1

                # 🔥 检查是否有 usage 信息（某些 API 会在最后的 chunk 中包含）
                if hasattr(chunk, "usage") and chunk.usage:
                    final_usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens or 0,
                        "completion_tokens": chunk.usage.completion_tokens or 0,
                        "total_tokens": chunk.usage.total_tokens or 0,
                    }
                    logger.debug(f"Got usage from chunk: {final_usage}")

                if not chunk.choices:
                    # 🔥 某些模型可能发送没有 choices 的 chunk（如心跳）
                    continue

                delta = chunk.choices[0].delta
                content = getattr(delta, "content", "") or ""
                # 推理模型的思考流（SGLang qwen3 parser: delta.reasoning_content；
                # 部分后端用 delta.thinking）——与正文 content 是两个独立通道。
                # 旧实现用 or 链把两者混进同一 content 流，思考退化噪声
                # （stopstopSTOP 等）由此污染正文视野与 Final Answer 解析；
                # 现按 kind 分流（structured-output-protocol Task 3）。
                reasoning_piece = (
                    getattr(delta, "reasoning_content", "")
                    or getattr(delta, "thinking", "")
                    or ""
                )
                finish_reason = chunk.choices[0].finish_reason

                # 流式 tool_calls 聚合（Task 7）：同一 index 的 id/name 首块到达、
                # arguments 增量拼接；缺失字段（后续块 id/name 为 None）不覆盖。
                delta_tool_calls = getattr(delta, "tool_calls", None)
                if delta_tool_calls:
                    for tc in delta_tool_calls:
                        tc_index = getattr(tc, "index", 0) or 0
                        slot = aggregated_tool_calls.setdefault(
                            tc_index, {"id": None, "name": None, "arguments": ""}
                        )
                        if getattr(tc, "id", None):
                            slot["id"] = tc.id
                        tc_function = getattr(tc, "function", None)
                        if tc_function is not None:
                            if getattr(tc_function, "name", None):
                                slot["name"] = tc_function.name
                            fn_arguments = getattr(tc_function, "arguments", None)
                            if fn_arguments:
                                slot["arguments"] += fn_arguments

                # 思考与正文同轮出现时各自独立 yield（两个 if，非 if/else）；
                # 思考阶段先于正文阶段到达，reasoning piece 先 yield。
                if reasoning_piece:
                    accumulated_reasoning += reasoning_piece
                    accumulated_all += reasoning_piece
                    yield {
                        "type": "token",
                        "kind": "reasoning",
                        "content": reasoning_piece,
                        "accumulated": accumulated_all,
                        "accumulated_content": accumulated_content,
                        "accumulated_reasoning": accumulated_reasoning,
                    }
                if content:
                    accumulated_content += content
                    accumulated_all += content
                    yield {
                        "type": "token",
                        "kind": "content",
                        "content": content,
                        "accumulated": accumulated_all,
                        "accumulated_content": accumulated_content,
                        "accumulated_reasoning": accumulated_reasoning,
                    }
                # 🔥 ENHANCED: 处理没有 content 但也没有 finish_reason 的情况
                # 某些模型（如智谱 GLM）可能在某些 chunk 中不返回内容

                if finish_reason:
                    # 流式完成
                    # 🔥 如果没有从 chunk 获取到 usage，进行估算
                    if not final_usage:
                        output_tokens_estimate = estimate_tokens(accumulated_all)
                        final_usage = {
                            "prompt_tokens": input_tokens_estimate,
                            "completion_tokens": output_tokens_estimate,
                            "total_tokens": input_tokens_estimate + output_tokens_estimate,
                        }
                        logger.debug(f"Estimated usage: {final_usage}")

                    # 🔥 ENHANCED: 如果累积内容为空但有 finish_reason，记录警告
                    if not accumulated_all and not aggregated_tool_calls:
                        logger.warning(f"Stream completed with no content after {chunk_count} chunks, finish_reason={finish_reason}")

                    done_chunk: Dict[str, Any] = {
                        "type": "done",
                        # 语义拆分（Task 3）：content 仅正文累计、reasoning 仅思考累计；
                        # accumulated 为两者拼接（兼容旧下游，Task 4 切换消费）
                        "content": accumulated_content,
                        "reasoning": accumulated_reasoning,
                        "accumulated": accumulated_all,
                        "usage": final_usage,
                        "finish_reason": finish_reason,
                    }
                    # Task 7：tool_calls 聚合结果随 done 输出（无 tool_calls 不带该键）
                    if aggregated_tool_calls:
                        done_chunk["tool_calls"] = [
                            aggregated_tool_calls[idx]
                            for idx in sorted(aggregated_tool_calls)
                        ]
                    yield done_chunk
                    finished = True
                    break

            # 🔥 ENHANCED: 如果循环结束但没有收到 finish_reason，也需要返回 done
            # （finished 守卫：已发过 finish_reason done 的流不得再补发 done，
            # 旧实现靠消费方拿到 done 后停止拉取隐式规避，全量排空时会重复发）
            if (accumulated_all or aggregated_tool_calls) and not finished:
                logger.warning(f"Stream ended without finish_reason, returning accumulated content ({len(accumulated_all)} chars)")
                if not final_usage:
                    output_tokens_estimate = estimate_tokens(accumulated_all)
                    final_usage = {
                        "prompt_tokens": input_tokens_estimate,
                        "completion_tokens": output_tokens_estimate,
                        "total_tokens": input_tokens_estimate + output_tokens_estimate,
                    }
                done_chunk = {
                    "type": "done",
                    "content": accumulated_content,
                    "reasoning": accumulated_reasoning,
                    "accumulated": accumulated_all,
                    "usage": final_usage,
                    "finish_reason": "complete",
                }
                if aggregated_tool_calls:
                    done_chunk["tool_calls"] = [
                        aggregated_tool_calls[idx]
                        for idx in sorted(aggregated_tool_calls)
                    ]
                yield done_chunk

        except litellm.exceptions.RateLimitError as e:
            # 速率限制错误 - 需要特殊处理
            logger.error(f"Stream rate limit error: {e}")
            error_msg = str(e)
            error_msg_lower = error_msg.lower()
            # 余额不足 / 配额用尽
            if any(keyword in error_msg_lower for keyword in _QUOTA_KEYWORDS):
                error_type = "quota_exceeded"
                user_message = "API 配额已用尽，请检查账户余额或升级计划"
            # 🔥 认证失败：LiteLLM 会把上游 401（authorization failed）也包装成 RateLimitError 抛出，
            # 必须在此识别并归为 authentication，否则误判为 rate_limit 导致 Orchestrator 走 30s×3 重试
            elif any(keyword in error_msg_lower for keyword in _AUTH_KEYWORDS):
                error_type = "authentication"
                user_message = "API Key 无效或已过期，请检查配置"
            else:
                error_type = "rate_limit"
                # 尝试从错误消息中提取重试时间
                import re
                retry_match = re.search(r"retry\s*(?:in|after)\s*(\d+(?:\.\d+)?)\s*s", error_msg, re.IGNORECASE)
                retry_seconds = float(retry_match.group(1)) if retry_match else 60
                user_message = f"API 调用频率超限，建议等待 {int(retry_seconds)} 秒后重试"

            output_tokens_estimate = estimate_tokens(accumulated_all) if accumulated_all else 0
            yield {
                "type": "error",
                "error_type": error_type,
                "error": error_msg,
                "user_message": user_message,
                "accumulated": accumulated_all,
                "usage": {
                    "prompt_tokens": input_tokens_estimate,
                    "completion_tokens": output_tokens_estimate,
                    "total_tokens": input_tokens_estimate + output_tokens_estimate,
                } if accumulated_all else None,
            }

        except litellm.exceptions.AuthenticationError as e:
            # 认证错误 - API Key 无效
            logger.error(f"Stream authentication error: {e}")
            yield {
                "type": "error",
                "error_type": "authentication",
                "error": str(e),
                "user_message": "API Key 无效或已过期，请检查配置",
                "accumulated": accumulated_all,
                "usage": None,
            }

        except litellm.exceptions.APIConnectionError as e:
            # 连接错误 - 网络问题
            logger.error(f"Stream connection error: {e}")
            yield {
                "type": "error",
                "error_type": "connection",
                "error": str(e),
                "user_message": "无法连接到 API 服务，请检查网络连接",
                "accumulated": accumulated_all,
                "usage": None,
            }

        except Exception as e:
            # 其他错误 - 检查是否是包装的速率限制错误
            error_msg = str(e)
            # 🔥 B4 修复: 某些 LLM 网关（如 opencode.ai）返回空错误体，str(e) 为空
            # 此时日志仅输出 "Stream error: " 无法诊断。补全异常类型与 args。
            # 注意: e.args 可能含敏感数据（API Key/URL），仅取类型名与 args 长度用于诊断，
            # 不直接拼入会返回给前端的 error_msg（避免 SSE 信息泄露）。
            if not error_msg.strip():
                diagnostic = f"{type(e).__name__} (args_count={len(e.args)})"
                logger.error(f"Stream error (empty message, diagnosed): {diagnostic} repr={repr(e)}")
                error_msg = diagnostic
            else:
                logger.error(f"Stream error: {error_msg}")

            # 检查是否是包装的速率限制错误（如 ServiceUnavailableError 包装 RateLimitError）
            is_rate_limit = any(keyword in error_msg.lower() for keyword in [
                "ratelimiterror", "rate limit", "429", "resource_exhausted",
                "quota exceeded", "too many requests"
            ])

            if is_rate_limit:
                # 按速率限制错误处理
                import re
                # 检查是否是配额用尽
                if any(keyword in error_msg.lower() for keyword in ["quota", "exceeded", "billing"]):
                    error_type = "quota_exceeded"
                    user_message = "API 配额已用尽，请检查账户余额或升级计划"
                else:
                    error_type = "rate_limit"
                    retry_match = re.search(r"retry\s*(?:in|after)\s*(\d+(?:\.\d+)?)\s*s", error_msg, re.IGNORECASE)
                    retry_seconds = float(retry_match.group(1)) if retry_match else 60
                    user_message = f"API 调用频率超限，建议等待 {int(retry_seconds)} 秒后重试"
            else:
                # 🔥 max_tokens 超限：解析服务商返回的限制值，给友好提示
                mt = _detect_max_tokens_error(error_msg)
                if mt:
                    limit, msg = mt
                    error_type = "bad_request"
                    user_message = msg
                else:
                    error_type = "unknown"
                    user_message = "LLM 调用发生错误，请重试"

            output_tokens_estimate = estimate_tokens(accumulated_all) if accumulated_all else 0
            yield {
                "type": "error",
                "error_type": error_type,
                "error": error_msg,
                "user_message": user_message,
                "accumulated": accumulated_all,
                "usage": {
                    "prompt_tokens": input_tokens_estimate,
                    "completion_tokens": output_tokens_estimate,
                    "total_tokens": input_tokens_estimate + output_tokens_estimate,
                } if accumulated_all else None,
            }

    async def validate_config(self) -> bool:
        """验证配置"""
        # Ollama 不需要 API Key
        if self.config.provider == LLMProvider.OLLAMA:
            if not self.config.model:
                raise LLMError("未指定 Ollama 模型", LLMProvider.OLLAMA)
            return True

        # 其他提供商需要 API Key
        if not self.config.api_key:
            raise LLMError(
                f"API Key未配置 ({self.config.provider.value})",
                self.config.provider,
            )

        # check for placeholder keys
        if "sk-your-" in self.config.api_key or "***" in self.config.api_key:
             raise LLMError(
                f"无效的 API Key (使用了占位符): {self.config.api_key[:10]}...",
                self.config.provider,
                401
            )

        if not self.config.model:
            raise LLMError(
                f"未指定模型 ({self.config.provider.value})",
                self.config.provider,
            )

        return True

    @classmethod
    def supports_provider(cls, provider: LLMProvider) -> bool:
        """检查是否支持指定的提供商"""
        return provider in cls.PROVIDER_PREFIX_MAP

