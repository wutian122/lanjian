from typing import List, Union, Optional
from pydantic import AnyHttpUrl, validator
from pydantic_settings import BaseSettings


# P0-1 已知弱值黑名单：任何撞上这些的都视为未修改的默认值，拒绝启动。
_INSECURE_SECRET_KEYS = {
    "changethis_in_production_to_a_long_random_string",
    "your-super-secret-key-change-this-in-production",
    "secret", "changeme", "changethis", "test", "dev", "development",
}


class Settings(BaseSettings):
    PROJECT_NAME: str = "蓝鉴"
    API_V1_STR: str = "/api/v1"

    # SECURITY
    # P0-1: 必须由环境变量提供，长度 >= 32 且不在弱值黑名单，否则应用启动失败。
    # 生成命令：python -c "import secrets; print(secrets.token_urlsafe(48))"
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30  # 30 minutes

    @validator("SECRET_KEY")
    def validate_secret_key(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError(
                "SECRET_KEY must be set via environment variable. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        if len(v) < 32:
            raise ValueError(
                f"SECRET_KEY too short ({len(v)} chars); must be >= 32. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        if v.strip().lower() in _INSECURE_SECRET_KEYS:
            raise ValueError(
                "SECRET_KEY is a well-known default and MUST be changed before starting."
            )
        return v

    # CORS
    BACKEND_CORS_ORIGINS: List[AnyHttpUrl] = []

    # P0-3: CORS 白名单（逗号分隔），优先级高于 BACKEND_CORS_ORIGINS。
    # 未配置或为空时，main.py 会关闭 allow_credentials 并把 origins 视为 [] 。
    # 生产必填，如：CORS_ALLOWED_ORIGINS=http://frontend-host-a.example.com,http://frontend-host-b.example.com
    CORS_ALLOWED_ORIGINS: str = ""

    # REGISTRATION
    ALLOW_PUBLIC_REGISTRATION: bool = False

    @validator("BACKEND_CORS_ORIGINS", pre=True)
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> Union[List[str], str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)

    # POSTGRES
    # P3-1: POSTGRES_PASSWORD 强制注入 —— 旧默认值 "postgres" 是所有 PG 教程都在用的
    # 弱密码；生产未改就发布等于给宿主 5432 开放渗透入口。
    # POSTGRES_USER 保留 "postgres" 默认（PG 官方超管名，公开信息），只锁定密码。
    POSTGRES_SERVER: str = "db"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str = "lanjian"
    DATABASE_URL: str | None = None

    @validator("POSTGRES_PASSWORD")
    def validate_postgres_password(cls, v: str) -> str:
        # P3-1: 拒绝空 / 弱值。策略比 SECRET_KEY 宽松 —— DB 密码本身不用来签名，
        # 只要不是"postgres" / "password" / "123456"这种一撞就中的即可。
        if not v or not isinstance(v, str):
            raise ValueError("POSTGRES_PASSWORD must be set via environment variable")
        weak = {"postgres", "password", "123456", "admin", "root", "test", "changeme", "postgres123"}
        if v.strip().lower() in weak:
            raise ValueError(
                "POSTGRES_PASSWORD is a well-known weak value and MUST be changed. "
                "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(24))\""
            )
        if len(v) < 12:
            raise ValueError(
                f"POSTGRES_PASSWORD too short ({len(v)} chars); must be >= 12."
            )
        return v

    @validator("DATABASE_URL", pre=True)
    def assemble_db_connection(cls, v: str | None, values: dict[str, any]) -> str:
        if isinstance(v, str):
            return v
        return str(f"postgresql+asyncpg://{values.get('POSTGRES_USER')}:{values.get('POSTGRES_PASSWORD')}@{values.get('POSTGRES_SERVER')}/{values.get('POSTGRES_DB')}")

    # LLM閰嶇疆
    LLM_PROVIDER: str = "openai"  # gemini, openai, claude, qwen, deepseek, zhipu, moonshot, baidu, minimax, doubao, ollama
    LLM_API_KEY: Optional[str] = None
    LLM_MODEL: Optional[str] = None  # 涓嶆寚瀹氭椂浣跨敤provider鐨勯粯璁ゆā鍨?
    LLM_BASE_URL: Optional[str] = None  # 鑷畾涔堿PI绔偣锛堝涓浆绔欙級
    LLM_TIMEOUT: int = 150  # 瓒呮椂鏃堕棿锛堢锛?
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 4096
    LLM_FREQUENCY_PENALTY: float = 0.0  # 代码审计需高确定性输出：过高的重复惩罚会让 LLM 回避已用字段名，破坏 JSON 稳定性

    # Agent 娴佸紡瓒呮椂閰嶇疆锛堢锛?
    LLM_FIRST_TOKEN_TIMEOUT: int = 180  # 首Token超时时间（秒），推理模型需要更长时间  # 绛夊緟棣栦釜Token鐨勮秴鏃舵椂闂?
    LLM_STREAM_TIMEOUT: int = 120  # 娴佸紡杈撳嚭涓袱涓猅oken涔嬮棿鐨勮秴鏃舵椂闂?
    SUB_AGENT_TIMEOUT_SECONDS: int = 1200  # 瀛怉gent瓒呮椂鏃堕棿锛?0鍒嗛挓锛?
    TOOL_TIMEOUT_SECONDS: int = 60  # 宸ュ叿鎵ц榛樿瓒呮椂鏃堕棿
    
    # 鍚凩LM鎻愪緵鍟嗙殑API Key閰嶇疆锛堝吋瀹瑰崟鐙厤缃級
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    CLAUDE_API_KEY: Optional[str] = None
    QWEN_API_KEY: Optional[str] = None
    DEEPSEEK_API_KEY: Optional[str] = None
    ZHIPU_API_KEY: Optional[str] = None
    MOONSHOT_API_KEY: Optional[str] = None
    BAIDU_API_KEY: Optional[str] = None  # 鏍煎紡: api_key:secret_key
    MINIMAX_API_KEY: Optional[str] = None
    DOUBAO_API_KEY: Optional[str] = None
    OLLAMA_BASE_URL: Optional[str] = "http://localhost:11434/v1"
    
    # GitHub閰嶇疆
    GITHUB_TOKEN: Optional[str] = None
    
    # GitLab閰嶇疆
    GITLAB_TOKEN: Optional[str] = None
    
    # Gitea閰嶇疆
    GITEA_TOKEN: Optional[str] = None
    
    # 鎵弿閰嶇疆
    MAX_ANALYZE_FILES: int = 0  # 鏈€澶у垎鏋愭枃浠舵暟锛?琛ㄧず鏃犻檺鍒?
    MAX_FILE_SIZE_BYTES: int = 200 * 1024  # 鏈€澶ф枃浠跺ぇ灏?200KB
    LLM_CONCURRENCY: int = 3  # LLM骞跺彂鏁?
    LLM_GAP_MS: int = 2000  # LLM璇锋眰闂撮殧锛堟绉掞級
    
    # ZIP鏂囦欢瀛樺偍閰嶇疆
    ZIP_STORAGE_PATH: str = "./uploads/zip_files"  # ZIP鏂囦欢瀛樺偍鐩綍
    
    # 杈撳嚭璇█閰嶇疆 - 鏀寔 zh-CN锛堜腑鏂囷級鍜?en-US锛堣嫳鏂囷級
    OUTPUT_LANGUAGE: str = "zh-CN"
    
    # ============ Agent 妯″潡閰嶇疆 ============

    # 宓屽叆妯″瀷閰嶇疆锛堢嫭绔嬩簬 LLM 閰嶇疆锛?
    EMBEDDING_PROVIDER: str = "openai"  # openai, azure, ollama, cohere, huggingface, jina, qwen
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_API_KEY: Optional[str] = None  # 宓屽叆妯″瀷涓撶敤 API Key锛堢暀绌哄垯浣跨敤 LLM_API_KEY锛?
    EMBEDDING_BASE_URL: Optional[str] = None  # 宓屽叆妯″瀷涓撶敤 Base URL锛堢暀绌轰娇鐢ㄦ彁渚涘晢榛樿鍦板潃锛?
    
    # 鍚戦噺鏁版嵁搴撻厤缃?
    VECTOR_DB_PATH: str = "./data/vector_db"  # 鍚戦噺鏁版嵁搴撴寔涔呭寲鐩綍

    # SSH閰嶇疆
    SSH_CONFIG_PATH: str = "./data/ssh"  # SSH閰嶇疆鐩綍锛堝瓨鍌╧nown_hosts绛夛級
    SSH_CLONE_TIMEOUT: int = 300  # SSH鍏嬮殕瓒呮椂鏃堕棿锛堢锛?
    SSH_TEST_TIMEOUT: int = 15  # SSH娴嬭瘯杩炴帴瓒呮椂鏃堕棿锛堢锛?
    SSH_CONNECT_TIMEOUT: int = 10  # SSH杩炴帴瓒呮椂鏃堕棿锛堢锛?
    
    # Agent 閰嶇疆
    AGENT_MAX_ITERATIONS: int = 50  # Agent 鏈€澶ц凯浠ｆ鏁?
    AGENT_TOKEN_BUDGET: int = 10_000_000  # Agent Token 预算（P1: 旧值 100000 远低于实际需求 5.6M~12.7M，上调到 10M）
    EMBEDDING_RATE_LIMIT: int = 5  # R3: embedding API 限流速率（req/s），默认 5 避免 siliconflow 429
    EMBEDDING_CACHE_DIR: str = ""  # R3: embedding 持久化缓存目录（空则用 VECTOR_DB_PATH 同级 embedding_cache/）
    AGENT_TIMEOUT_SECONDS: int = 1800  # Agent 瓒呮椂鏃堕棿锛?0鍒嗛挓锛?
    
    # 娌欑閰嶇疆锛堝繀椤伙級
    SANDBOX_IMAGE: str = "wutian449/lanjian-sandbox:latest"  # 娌欑 Docker 闀滃儚
    SANDBOX_MEMORY_LIMIT: str = "512m"  # 娌欑鍐呭瓨闄愬埗
    SANDBOX_CPU_LIMIT: float = 1.0  # 娌欑 CPU 闄愬埗
    SANDBOX_TIMEOUT: int = 60  # 娌欑鍛戒护瓒呮椂锛堢锛?
    SANDBOX_NETWORK_MODE: str = "none"  # 娌欑缃戠粶妯″紡 (none, bridge)
    SANDBOX_NETWORK_ENABLED: bool = False  # 沙箱是否允许联网（默认关闭）
    
    # RAG 閰嶇疆
    RAG_CHUNK_SIZE: int = 1500  # 浠ｇ爜鍧楀ぇ灏忥紙Token锛?
    RAG_CHUNK_OVERLAP: int = 50  # 浠ｇ爜鍧楅噸鍙狅紙Token锛?
    RAG_TOP_K: int = 10  # 妫€绱㈣繑鍥炴暟閲?

    # ============ v3.1 Fusion: code-audit-main 铻嶅悎閰嶇疆 ============

    # Feature Flags 鈥?鍚勭粍浠跺彲鐙珛寮€鍏?
    ENABLE_COVERAGE_TRACKING: bool = True       # D1-D10 瑕嗙洊鐭╅樀
    ENABLE_ANTI_HALLUCINATION: bool = True      # 澧炲己闃插够瑙夎鍒?
    ENABLE_AGENT_CONTRACT: bool = True          # Agent 鍚堢害绾︽潫
    ENABLE_CONTROL_DRIVEN_AUDIT: bool = True    # 鎺у埗椹卞姩瀹¤ (D3/D9)
    ENABLE_ATTACK_CHAINS: bool = False          # 鏀诲嚮閾炬嫾瑁?(瀹為獙鎬?
    ENABLE_FP_KILL_SWITCH: bool = False         # 璇姤 Kill Switch (瀹為獙鎬?

    # Agent 鍚堢害閰嶇疆 (鏉ヨ嚜 code-audit-main)
    AGENT_MAX_TURNS: int = 50                   # 姣廇gent鏈€澶ц疆娆?
    DEEP_AUDIT_R1_TURNS: int = 25               # 娣卞害瀹¤ R1 杞
    DEEP_AUDIT_R2_TURNS: int = 20               # 娣卞害瀹¤ R2 杞
    COVERAGE_TERMINATION_THRESHOLD: int = 8     # 瑕嗙洊鐭╅樀缁堟闃堝€?(/10)
    COVERAGE_MIN_FINDINGS: int = 10             # 鏈€灏戝彂鐜版暟鎵嶅彲杩涘叆 REPORT

    class Config:
        case_sensitive = True
        env_file = ".env"
        extra = "ignore"  # 蹇界暐棰濆鐨勭幆澧冨彉閲忥紙濡?VITE_* 鍓嶇鍙橀噺锛?


settings = Settings()

