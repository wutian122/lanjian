/**
 * Task 17 静态契约测试：详情页沙箱证据三标记 + infra_error 展示
 *
 * 前端无 vitest/jest 基建（离线环境），沿用仓库零依赖静态断言先例
 * （budgetConfig.contract.test.mjs / contentStreamSharding.test.mjs：读源码断言契约），
 * 直接用 node 运行：
 *   node frontend/src/pages/AgentAudit/components/__tests__/sandboxEvidenceMarkers.contract.test.mjs
 *
 * 覆盖 openspec 变更 sandbox-verification-hard-gate Task 17
 * （spec Requirement「沙箱证据关键语义标记 MUST 前端可见」）：
 * - attempt 卡片四语义徽章：
 *   fabricated      → 红「伪造证据已排除」（LLM 声称确认带伪造标记，被排除出判定）
 *   static_evidence → 蓝「演示性静态确认」（确定性 PoC 模板 STATIC 标记，非真实动态利用）
 *   poc_error       → 琥珀「验证器崩溃」（含 poc_error_type 时附类型）
 *   infra_error     → 灰/红「沙箱环境故障」（Task 1：Docker/镜像故障，未进容器）
 * - finding 级：verification_status=needs_context 且 verification_note 含
 *   infra_error=True（后端 compute_verification_status infra 分支 notes 落库形态）
 *   时，FindingDetailPanel 显示「沙箱环境故障（未能验证）」提示
 * - SandboxAttempt 类型字段与后端 _record_sandbox_attempt 产物结构对齐
 * - 与 Task 16 的 sandbox_skip_reason 展示共存不冲突
 */
import { readFileSync, existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
// frontend/src/pages/AgentAudit/components/__tests__ → frontend/src
const srcDir = resolve(__dirname, '..', '..', '..', '..');

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

function readSrc(rel) {
  return readFileSync(resolve(srcDir, rel), 'utf-8');
}

// 中文文案断言：源码中中文可能直接书写（FindingDetailPanel）或按文件既有风格
// 存为 \uXXXX 转义（FindingSandboxEvidence 原文件全转义，写入链路沿用该风格）——
// 两种形式 JS 运行时语义等价，断言只约束文案语义，不绑定存储编码。
function zhPattern(s) {
  return s
    .split('')
    .map((ch) => {
      const esc = ch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const code = ch.codePointAt(0);
      if (code > 0x7f) {
        const hex = code.toString(16).padStart(4, '0');
        return `(?:${esc}|\\\\u${hex})`;
      }
      return esc;
    })
    .join('');
}

function zhRe(s, flags = '') {
  return new RegExp(zhPattern(s), flags);
}

// ============================================================
// 1. SandboxAttempt 类型补四语义字段（与后端 attempt 结构对齐）
// ============================================================
console.log('\n[1] pages/AgentAudit/types.ts SandboxAttempt 类型字段');
{
  const types = readSrc('pages/AgentAudit/types.ts');
  const block = types.match(/export interface SandboxAttempt \{[\s\S]*?\}/);
  assert(block !== null, 'SandboxAttempt 接口存在');
  if (block) {
    assert(/fabricated\??:\s*boolean/.test(block[0]),
      "SandboxAttempt.fabricated?: boolean（后端 _record_sandbox_attempt 伪造降级标记）");
    assert(/static_evidence\??:\s*boolean/.test(block[0]),
      "SandboxAttempt.static_evidence?: boolean（演示性静态确认降档）");
    assert(/poc_error\??:\s*boolean/.test(block[0]),
      "SandboxAttempt.poc_error?: boolean（验证器/PoC 崩溃标记）");
    assert(/poc_error_type\??:\s*(string\s*\|\s*null|string)/.test(block[0]),
      "SandboxAttempt.poc_error_type?: string | null（崩溃类型，后端值 'pre-generated PoC crashed'）");
    assert(/infra_error\??:\s*boolean/.test(block[0]),
      "SandboxAttempt.infra_error?: boolean（Task 1 沙箱基础设施故障标记）");
  }
}

// ============================================================
// 2. FindingSandboxEvidence attempt 卡片四徽章渲染分支
// ============================================================
console.log('\n[2] FindingSandboxEvidence.tsx 四语义徽章');
const evidencePath = 'pages/AgentAudit/components/FindingSandboxEvidence.tsx';
{
  assert(existsSync(resolve(srcDir, evidencePath)), 'FindingSandboxEvidence.tsx 存在');
  const ev = readSrc(evidencePath);

  // 字段读取分支（与后端 attempt dict 键名逐字一致；\b 防 fabricated_XXX 类变形漏网）
  assert(/attempt\.fabricated\b/.test(ev), '渲染分支读取 attempt.fabricated');
  assert(/attempt\.static_evidence\b/.test(ev), '渲染分支读取 attempt.static_evidence');
  assert(/attempt\.poc_error\b/.test(ev), '渲染分支读取 attempt.poc_error');
  assert(/attempt\.infra_error\b/.test(ev), '渲染分支读取 attempt.infra_error');

  // 文案（用户可见语义；中文直接书写或 \u 转义两种存储形式均接受）
  assert(zhRe('伪造证据已排除').test(ev), 'fabricated → 红徽章文案「伪造证据已排除」');
  assert(zhRe('演示性静态确认').test(ev), 'static_evidence → 蓝徽章文案「演示性静态确认」');
  assert(zhRe('验证器崩溃').test(ev), 'poc_error → 琥珀徽章文案「验证器崩溃」');
  assert(zhRe('沙箱环境故障').test(ev), 'infra_error → 徽章文案「沙箱环境故障」');

  // poc_error_type 附属展示（含类型时附类型）
  assert(/attempt\.poc_error_type/.test(ev),
    'poc_error 徽章附属 attempt.poc_error_type（崩溃类型，非空时展示）');

  // 徽章颜色语义（Tailwind 类）：fabricated 红 / static_evidence 蓝 / poc_error 琥珀 / infra 灰或红。
  // 断言字段条件渲染后紧跟其徽章 span 的配色类（短跨度，避免 title 转义文本干扰）。
  assert(/attempt\.fabricated\b[\s\S]{0,200}?bg-red-/.test(ev),
    'fabricated 徽章红色样式类（bg-red-）');
  assert(/attempt\.static_evidence\b[\s\S]{0,200}?bg-blue-/.test(ev),
    'static_evidence 徽章蓝色样式类（bg-blue-）');
  assert(/attempt\.poc_error\b[\s\S]{0,200}?bg-amber-/.test(ev),
    'poc_error 徽章琥珀色样式类（bg-amber-）');
  assert(/attempt\.infra_error\b[\s\S]{0,200}?bg-gray-/.test(ev),
    'infra_error 徽章灰色样式类（bg-gray-）');

  // 四徽章均为条件渲染（字段为真才显示），不影响既有 success/weak_evidence 徽章
  assert(/attempt\.fabricated\b[^\n]*&&/.test(ev),
    'fabricated 徽章条件渲染（真值才显示）');
  assert(/attempt\.static_evidence\b[^\n]*&&/.test(ev),
    'static_evidence 徽章条件渲染');
  assert(/attempt\.poc_error\b[^\n]*&&/.test(ev),
    'poc_error 徽章条件渲染');
  assert(/attempt\.infra_error\b[^\n]*&&/.test(ev),
    'infra_error 徽章条件渲染');
}

// ============================================================
// 3. FindingDetailPanel finding 级 infra 故障提示
// ============================================================
console.log('\n[3] FindingDetailPanel.tsx finding 级 infra 提示');
{
  const panel = readSrc('pages/AgentAudit/components/FindingDetailPanel.tsx');

  // 判定：verification_status=needs_context 且 verification_note 含 infra_error=True
  // （后端 notes 落库形态："reason=沙箱环境故障…; infra_error=True"）
  assert(/needs_context/.test(panel), '面板识别 needs_context 终态');
  assert(/infra_error\s*=\s*True/.test(panel) || /infra_error=True/.test(panel),
    '判定 verification_note 含 infra_error=True（后端 compute notes 键值落库形态）');
  assert(/verification_note/.test(panel),
    '读取 verification_result.verification_note（infra 诊断 reason 落库字段）');

  // 用户可见提示文案
  assert(zhRe('沙箱环境故障（未能验证）').test(panel),
    'finding 级提示文案「沙箱环境故障（未能验证）」');

  // 与 Task 16 skip_reason 展示共存：两个独立条件块，互不覆盖
  assert(/sandbox_skip_reason/.test(panel), 'Task 16 skip_reason 展示块保留（不冲突）');
  const skipBlock = panel.match(/sandbox_skip_reason[\s\S]{0,200}?&&/);
  const infraBlock = panel.match(/infra_error=True|infra_error\s*=\s*True/);
  assert(skipBlock !== null && infraBlock !== null,
    'skip_reason 块与 infra 提示块为独立条件（可同时出现：豁免 vs 环境故障语义不混淆）');
}

// ============================================================
// 汇总
// ============================================================
console.log(`\n${'='.repeat(60)}`);
console.log(`结果: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
