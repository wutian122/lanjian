// 搴旂敤甯搁噺瀹氫箟

// 鏀寔鐨勭紪绋嬭瑷€
export const SUPPORTED_LANGUAGES = [
  'javascript',
  'typescript',
  'python',
  'java',
  'go',
  'rust',
  'cpp',
  'csharp',
  'php',
  'ruby',
  'swift',
  'kotlin',
] as const;

// 闂绫诲瀷
export const ISSUE_TYPES = {
  BUG: 'bug',
  SECURITY: 'security',
  PERFORMANCE: 'performance',
  STYLE: 'style',
  MAINTAINABILITY: 'maintainability',
} as const;

// 闂涓ラ噸绋嬪害
export const SEVERITY_LEVELS = {
  CRITICAL: 'critical',
  HIGH: 'high',
  MEDIUM: 'medium',
  LOW: 'low',
} as const;

// 浠诲姟鐘舵€?
export const TASK_STATUS = {
  PENDING: 'pending',
  RUNNING: 'running',
  COMPLETED: 'completed',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
} as const;

// 鐢ㄦ埛瑙掕壊
export const USER_ROLES = {
  ADMIN: 'admin',
  MEMBER: 'member',
} as const;

// 椤圭洰鎴愬憳瑙掕壊
export const PROJECT_ROLES = {
  OWNER: 'owner',
  ADMIN: 'admin',
  MEMBER: 'member',
  VIEWER: 'viewer',
} as const;

// 椤圭洰鏉ユ簮绫诲瀷
export const PROJECT_SOURCE_TYPES = {
  REPOSITORY: 'repository',
  ZIP: 'zip',
} as const;

// 鍒嗘瀽娣卞害
export const ANALYSIS_DEPTH = {
  BASIC: 'basic',
  STANDARD: 'standard',
  DEEP: 'deep',
} as const;

// 榛樿閰嶇疆锛堜笌鍚庣瀵归綈锛?
export const DEFAULT_CONFIG = {
  MAX_FILE_SIZE: 200 * 1024, // 200KB (瀵归綈鍚庣 MAX_FILE_SIZE_BYTES)
  MAX_FILES_PER_SCAN: 0, // 瀵归綈鍚庣 MAX_ANALYZE_FILES锛?琛ㄧず鏃犻檺鍒?
  ANALYSIS_TIMEOUT: 30000, // 30绉?
  DEBOUNCE_DELAY: 300, // 300ms
} as const;

// ProjectDetail 椤甸潰涓撶敤甯搁噺
// 鍗曡姹傝秴鏃讹紙ms锛夛細閬垮厤澶栭儴/鍚庣鍗℃瀵艰嚧 UI 闀挎椂闂存棤鍝嶅簲
export const PROJECT_DETAIL_REQUEST_TIMEOUT_MS = 12_000;
// Issues/Findings 闈㈡澘锛氭渶澶氭姄鍙栨渶杩?N 涓凡瀹屾垚浠诲姟
export const PROJECT_DETAIL_ISSUES_MAX_TASKS = 20;
// Issues/Findings 鎷夊彇骞跺彂搴︼細闃叉瀵瑰悗绔舰鎴愮獊鍙戝帇鍔?
export const PROJECT_DETAIL_ISSUES_FETCH_CONCURRENCY = 5;

// API 绔偣
export const API_ENDPOINTS = {
  PROJECTS: '/api/projects',
  AUDIT_TASKS: '/api/audit-tasks',
  INSTANT_ANALYSIS: '/api/instant-analysis',
  USERS: '/api/users',
} as const;

// 鏈湴瀛樺偍閿悕
export const STORAGE_KEYS = {
  THEME: 'lanjian-theme',
  USER_PREFERENCES: 'lanjian-preferences',
  RECENT_PROJECTS: 'lanjian-recent-projects',
} as const;

// 瀵煎嚭椤圭洰绫诲瀷鐩稿叧甯搁噺
export * from './projectTypes';
