/**
 * 三字段分流渲染（structured-output-protocol Task 5）静态契约测试
 *
 * 前端无 vitest/jest 基建（离线环境 tsx 亦不可用），沿用仓库既有静态断言传统
 * （对照 useResilientStream.reconnect.test.tsx：读源码断言契约），本测试零依赖，
 * 直接用 node 运行：
 *   node frontend/src/pages/AgentAudit/hooks/__tests__/contentStreamSharding.test.mjs
 *
 * 覆盖 spec thinking-stream-separation 第二个 Requirement「前端 SHALL 按字段分流渲染」：
 * - content_token/content_end 事件从传输层到页面回调全链路接线
 * - 正文流成形为独立 'content' 日志（与思考 'thinking' 视觉区分）
 * - 历史回放用 content_end 的 metadata.accumulated 重建正文日志，
 *   content_token 显式跳过（防噪声行）
 * - 旧后端无 content_* 事件时 thinking 路径不变（兼容）
 */
import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const auditDir = resolve(__dirname, '..', '..');
const sharedApiDir = resolve(auditDir, '..', '..', 'shared', 'api');
const backendEventManager = resolve(
  auditDir, '..', '..', '..', '..', 'backend', 'app', 'services', 'agent', 'event_manager.py'
);

let passed = 0;
let failed = 0;

function assert(condition, message) {
  if (condition) {
    passed++;
    console.log(`  PASS: ${message}`);
  } else {
    failed++;
    console.error(`  FAIL: ${message}`);
  }
}

function read(relFromAuditDir) {
  return readFileSync(resolve(auditDir, relFromAuditDir), 'utf-8');
}

// ============================================================
// 1. agentStream.ts：事件类型 + 回调 + switch 分流
// ============================================================
console.log('\n[1] agentStream.ts 传输层分流');
{
  const src = readFileSync(resolve(sharedApiDir, 'agentStream.ts'), 'utf-8');

  assert(/'content_token'/.test(src), "StreamEventType 登记 'content_token'");
  assert(/'content_end'/.test(src), "StreamEventType 登记 'content_end'");
  assert(/onContentToken\?\s*:/.test(src), 'StreamOptions 声明 onContentToken 回调');
  assert(/onContentEnd\?\s*:/.test(src), 'StreamOptions 声明 onContentEnd 回调');

  // content_token case：双位置兼容（顶层或 metadata），与 thinking_token 同模式
  const contentTokenCase = src.match(/case 'content_token':[\s\S]*?break;/);
  assert(contentTokenCase !== null, "handleEvent 含 case 'content_token'");
  if (contentTokenCase) {
    const body = contentTokenCase[0];
    assert(/metadata\?\.token/.test(body), 'content_token 从 metadata.token 兼容提取增量');
    assert(/metadata\?\.accumulated/.test(body), 'content_token 从 metadata.accumulated 兼容提取全文');
    assert(/onContentToken\?\.\(/.test(body), 'content_token 分发到 onContentToken 回调');
  }

  const contentEndCase = src.match(/case 'content_end':[\s\S]*?break;/);
  assert(contentEndCase !== null, "handleEvent 含 case 'content_end'");
  if (contentEndCase) {
    const body = contentEndCase[0];
    assert(/metadata\?\.accumulated/.test(body), 'content_end 从 metadata.accumulated 取全文');
    assert(/onContentEnd\?\.\(/.test(body), 'content_end 分发到 onContentEnd 回调');
  }

  // thinking 路径保持不变（旧后端兼容）
  assert(/case 'thinking_token':/.test(src), 'thinking_token case 保留（旧后端兼容）');
  assert(/case 'thinking_end':/.test(src), 'thinking_end case 保留（旧后端兼容）');
}

// ============================================================
// 2. useResilientStream.ts：生产路径 switch 分流
// ============================================================
console.log('\n[2] useResilientStream.ts 生产路径分流');
{
  const src = read('hooks/useResilientStream.ts');

  const contentTokenCase = src.match(/case 'content_token':[\s\S]*?break;/);
  assert(contentTokenCase !== null, "生产路径 handleEvent 含 case 'content_token'");
  if (contentTokenCase) {
    const body = contentTokenCase[0];
    assert(/opts\.onContentToken\?\.\(/.test(body), 'content_token 分发到 opts.onContentToken');
    assert(/metadata\?\.accumulated/.test(body), 'content_token 双位置兼容取 accumulated');
  }

  const contentEndCase = src.match(/case 'content_end':[\s\S]*?break;/);
  assert(contentEndCase !== null, "生产路径 handleEvent 含 case 'content_end'");
  if (contentEndCase) {
    assert(/opts\.onContentEnd\?\.\(/.test(contentEndCase[0]), 'content_end 分发到 opts.onContentEnd');
  }
}

// ============================================================
// 3. index.tsx：正文日志成形 + 历史回放
// ============================================================
console.log('\n[3] index.tsx 正文流日志与回放');
{
  const src = read('index.tsx');

  assert(/onContentToken:\s*\(/.test(src), 'streamOptions 提供 onContentToken 回调');
  assert(/onContentEnd:\s*\(/.test(src), 'streamOptions 提供 onContentEnd 回调');

  // onContentToken：首次 ADD_LOG type:'content'，后续 updateLog
  const onContentToken = src.match(/onContentToken:[\s\S]*?\n    \},/);
  assert(onContentToken !== null, 'onContentToken 回调体存在');
  if (onContentToken) {
    const body = onContentToken[0];
    assert(/type:\s*'content'/.test(body), "onContentToken 首次 ADD_LOG type:'content'");
    assert(/title:\s*'回答'/.test(body), "正文日志标题为 '回答'");
    assert(/isStreaming:\s*true/.test(body), '正文日志流式中标记 isStreaming:true');
    assert(/updateLog\(/.test(body), 'onContentToken 后续 token 走 updateLog');
    assert(/setCurrentContentId|getCurrentContentId/.test(body), 'onContentToken 跟踪正文日志 id');
  }

  // onContentEnd：全文落定 + isStreaming:false
  const onContentEnd = src.match(/onContentEnd:[\s\S]*?\n    \},/);
  assert(onContentEnd !== null, 'onContentEnd 回调体存在');
  if (onContentEnd) {
    const body = onContentEnd[0];
    assert(/isStreaming:\s*false/.test(body), 'onContentEnd 置 isStreaming:false（正文流成形）');
    assert(/updateLog\(/.test(body), 'onContentEnd 以全文 updateLog');
  }

  // 历史回放 switch：content_end 用 metadata.accumulated 重建正文日志
  const replayContentEnd = src.match(/case 'content_end':[\s\S]*?break;/);
  assert(replayContentEnd !== null, "历史回放 switch 含 case 'content_end'（拦截'正文输出完成'噪声行）");
  if (replayContentEnd) {
    const body = replayContentEnd[0];
    assert(/metadata\?\.accumulated/.test(body), '回放 content_end 用 metadata.accumulated 全文重建');
    assert(/type:\s*'content'/.test(body), "回放 content_end 重建 type:'content' 日志");
  }

  // 回放 content_token 显式跳过（不落库本不该出现，防御性 case 防噪声）
  const replayContentToken = src.match(/case 'content_token':[\s\S]*?break;/);
  assert(replayContentToken !== null, "历史回放 switch 显式跳过 'content_token'（防噪声）");
  if (replayContentToken) {
    assert(!/dispatch|ADD_LOG/.test(replayContentToken[0]),
      "回放 content_token case 体内不产生任何日志");
  }

  // 思考回放路径保持
  assert(/case 'thinking_end':/.test(src), '回放 thinking_end case 保留');
}

// ============================================================
// 4. LogEntry.tsx / constants.tsx / types.ts：content 日志类型与视觉区分
// ============================================================
console.log('\n[4] LogEntry 视觉分流');
{
  const entry = read('components/LogEntry.tsx');
  const constants = read('constants.tsx');
  const types = read('types.ts');

  assert(/content:\s*'回答'/.test(entry), "LOG_TYPE_LABELS 含 content:'回答'");
  assert(/thinking:\s*'思考'/.test(entry), "思考标签 '思考' 保留（紫色区分）");
  assert(/content:\s*'bg-/.test(entry), 'typeLabelColors 含 content 徽章配色');
  assert(/content:\s*\{/.test(constants), 'LOG_TYPE_CONFIG 含 content 图标/配色配置');
  assert(/\| 'content'/.test(types), "LogType 联合类型含 'content'");

  // 正文内容区始终可见（与 thinking 同构，不折叠隐藏）
  assert(/isContent\s*=\s*item\.type\s*===\s*'content'/.test(entry), "LogEntry 识别 isContent");
  assert(/showContent[\s\S]*?isContent/.test(entry), '正文内容默认可见（showContent 含 isContent）');

  // 流式光标颜色随类型区分（不得对 content 也用 violet）
  const cursor = entry.match(/item\.isStreaming\s*&&\s*\([\s\S]*?\)/);
  assert(cursor !== null, '流式光标存在');
  if (cursor) {
    assert(/emerald|green|primary|teal/.test(cursor[0]) && /violet/.test(cursor[0]),
      '流式光标按思考/正文区分颜色（紫 vs 正文色）');
  }
}

// ============================================================
// 5. useAgentAuditState.ts：正文日志 id 跟踪
// ============================================================
console.log('\n[5] useAgentAuditState 正文日志 id 跟踪');
{
  const src = read('hooks/useAgentAuditState.ts');
  assert(/currentContentId\s*=\s*useRef/.test(src), 'useAgentAuditState 持有 currentContentId ref');
  assert(/setCurrentContentId/.test(src), '导出 setCurrentContentId');
  assert(/getCurrentContentId/.test(src), '导出 getCurrentContentId');
  const reset = src.match(/const reset = useCallback\([\s\S]*?\}, \[\]\);/);
  assert(reset !== null && /currentContentId\.current\s*=\s*null/.test(reset[0]),
    'reset 清空 currentContentId');
}

// ============================================================
// 6. event_manager.py：coalesce 常量通用化重命名（Task 4 审查承接项）
// ============================================================
console.log('\n[6] event_manager.py 常量重命名');
{
  const src = readFileSync(backendEventManager, 'utf-8');
  assert(!/THINKING_TOKEN_COALESCE/.test(src), 'THINKING_TOKEN_COALESCE_* 旧名已全部消除');
  assert(/TOKEN_COALESCE_WINDOW\s*=/.test(src), 'TOKEN_COALESCE_WINDOW 定义存在');
  assert(/TOKEN_COALESCE_MIN_CHARS\s*=/.test(src), 'TOKEN_COALESCE_MIN_CHARS 定义存在');
  assert(/self\.TOKEN_COALESCE_WINDOW/.test(src), '聚合窗口引用新名');
  assert(/self\.TOKEN_COALESCE_MIN_CHARS/.test(src), '聚合字符阈值引用新名');
}

// ============================================================
// 汇总
// ============================================================
console.log(`\n${'='.repeat(60)}`);
console.log(`结果: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
