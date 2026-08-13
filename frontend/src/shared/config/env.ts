/**
 * 鍓嶇鐜鍙橀噺閰嶇疆
 * 
 * 娉ㄦ剰锛氬湪 refactor/backend 鍒嗘敮涓紝鎵€鏈?LLM 鍒嗘瀽閮藉湪鍚庣杩涜銆?
 * LLM 閰嶇疆淇濆瓨鍦ㄥ悗绔暟鎹簱锛堥€氳繃 /api/v1/config/me API锛夛紝
 * 杩欓噷鍙繚鐣欏墠绔簲鐢ㄦ湰韬渶瑕佺殑閰嶇疆銆?
 */

// ==================== 搴旂敤閰嶇疆 ====================
export const env = {
  // 搴旂敤ID
  APP_ID: import.meta.env.VITE_APP_ID || 'lanjian',
  
  // API 鍩虹URL
  API_BASE_URL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  
  // ==================== 寮€鍙戠幆澧冩爣璇?====================
  isDev: import.meta.env.DEV,
  isProd: import.meta.env.PROD,
  
  // 娉ㄦ剰锛欸itHub/GitLab Token 绛夌涓夋柟鏈嶅姟閰嶇疆宸茬Щ鑷冲悗绔暟鎹簱
  // 鐢ㄦ埛鍙互閫氳繃 SystemConfig 椤甸潰閰嶇疆锛屽悗绔垎鏋愭椂浼氳嚜鍔ㄤ娇鐢?
} as const;
