/**
 * Task 16 静态契约测试：创建表单预算入口 + sandbox_skip_reason 展示承接
 *
 * 前端无 vitest/jest 基建（离线环境），沿用仓库零依赖静态断言先例
 * （contentStreamSharding.test.mjs：读源码断言契约），直接用 node 运行：
 *   node frontend/src/components/common/__tests__/budgetConfig.contract.test.mjs
 *
 * 覆盖 openspec 变更 sandbox-verification-hard-gate Task 16：
 * - 两处创建对话框（agent/CreateAgentTaskDialog、audit/CreateTaskDialog）
 *   高级选项区提供"超时时间（分钟）/最大迭代次数"入口
 * - 默认值语义裁决：留空不传字段（timeout_seconds 走全局 7200s、max_iterations
 *   走后端默认 50），仅用户显式修改才传——保留 Task 1 "NULL 回退全局"语义
 * - 范围校验与后端 AgentTaskCreate 约束对齐（timeout_seconds ge=60 le=7200
 *   → 分钟 1-120；max_iterations ge=1 le=200），范围外提交前端拦截
 * - Task 8 review M2 承接：VerificationResult 类型补 sandbox_skip_reason，
 *   FindingDetailPanel 有 skip_reason 时展示中文释义
 */
import { readFileSync, existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
// frontend/src/components/common/__tests__ → frontend/src
const srcDir = resolve(__dirname, '..', '..', '..');

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

// ============================================================
// 0. buildBudgetPayload 运行时行为（Node 原生 TS 类型剥离，直读源码）
// ============================================================
console.log('\n[0] buildBudgetPayload 运行时行为');
const budgetMod = await import(resolve(srcDir, 'shared/utils/budgetConfig.ts'))
  .catch(() => null);
const buildBudgetPayload = budgetMod?.buildBudgetPayload;
const BUDGET_LIMITS = budgetMod?.BUDGET_LIMITS;

if (buildBudgetPayload) {
  // 默认不传语义：全空 → payload 无字段（timeout 走全局 7200s、迭代走后端默认 50）
  {
    const r = buildBudgetPayload({ timeoutMinutes: "", maxIterations: "" });
    assert(r.ok === true && Object.keys(r.payload).length === 0,
      '全空输入：ok:true 且 payload 不含任何字段（保留 NULL 回退全局语义）');
  }
  {
    const r = buildBudgetPayload({ timeoutMinutes: "  ", maxIterations: "" });
    assert(r.ok === true && r.payload.timeout_seconds === undefined,
      '纯空白输入视同留空（trim 后不传）');
  }
  // 分钟 ×60 转秒，边界值
  {
    const r = buildBudgetPayload({ timeoutMinutes: "120", maxIterations: "" });
    assert(r.ok === true && r.payload.timeout_seconds === 7200,
      '120 分钟 → timeout_seconds=7200（与全局 agentTimeout 一致）');
  }
  {
    const r = buildBudgetPayload({ timeoutMinutes: "1", maxIterations: "" });
    assert(r.ok === true && r.payload.timeout_seconds === 60,
      '1 分钟（下界）→ timeout_seconds=60（后端 ge=60 对齐）');
  }
  {
    const r = buildBudgetPayload({ timeoutMinutes: "", maxIterations: "50" });
    assert(r.ok === true && r.payload.max_iterations === 50 && r.payload.timeout_seconds === undefined,
      '迭代 50 → max_iterations=50，未填的超时不传');
  }
  {
    const r = buildBudgetPayload({ timeoutMinutes: "30", maxIterations: "200" });
    assert(r.ok === true && r.payload.timeout_seconds === 1800 && r.payload.max_iterations === 200,
      '两字段同填：30 分钟=1800s、迭代 200（上界）');
  }
  // 范围外 / 非法值拦截
  for (const [vals, label] of [
    [{ timeoutMinutes: "121", maxIterations: "" }, "超时 121 分钟（超上界 120）"],
    [{ timeoutMinutes: "0", maxIterations: "" }, "超时 0 分钟（低下界 1）"],
    [{ timeoutMinutes: "", maxIterations: "201" }, "迭代 201（超上界 200）"],
    [{ timeoutMinutes: "", maxIterations: "0" }, "迭代 0（低下界 1）"],
    [{ timeoutMinutes: "1.5", maxIterations: "" }, "超时 1.5（非整数）"],
    [{ timeoutMinutes: "abc", maxIterations: "" }, "超时非数字"],
  ]) {
    const r = buildBudgetPayload(vals);
    assert(r.ok === false && r.errors.length > 0, `范围外拦截：${label} → ok:false 带错误信息`);
  }
  // 常量与后端约束对齐
  assert(BUDGET_LIMITS.timeoutMinutes.min * 60 === 60 && BUDGET_LIMITS.timeoutMinutes.max * 60 === 7200,
    '常量边界 ×60 = 后端 60-7200 秒约束');
  assert(BUDGET_LIMITS.maxIterations.min === 1 && BUDGET_LIMITS.maxIterations.max === 200,
    '迭代常量边界 1-200 = 后端 ge=1 le=200');
} else {
  assert(false, 'budgetConfig.ts 可被 Node 直接导入运行（TS 类型剥离）');
}

// ============================================================
// 1. 共享预算模块：范围常量、提交体构造、默认不传语义
// ============================================================
console.log('\n[1] shared/utils/budgetConfig.ts 预算纯逻辑');
const budgetUtilPath = resolve(srcDir, 'shared/utils/budgetConfig.ts');
assert(existsSync(budgetUtilPath), 'budgetConfig.ts 模块存在');

if (existsSync(budgetUtilPath)) {
  const util = readSrc('shared/utils/budgetConfig.ts');

  // 范围常量与后端 AgentTaskCreate 对齐（timeout 60-7200s → 分钟 1-120；iterations 1-200）
  assert(/timeoutMinutes[\s\S]*?min:\s*1\b/.test(util) && /timeoutMinutes[\s\S]*?max:\s*120\b/.test(util),
    '超时分钟范围常量 min=1 max=120（对应后端 60-7200 秒）');
  assert(/maxIterations[\s\S]*?min:\s*1\b/.test(util) && /maxIterations[\s\S]*?max:\s*200\b/.test(util),
    '最大迭代范围常量 min=1 max=200（与后端 ge=1 le=200 对齐）');
  assert(/default:\s*120/.test(util), '超时默认值 120 分钟（=全局 7200s）');
  assert(/default:\s*50/.test(util), '迭代默认值 50（=后端 Field 默认）');

  // 提交体构造：分钟 ×60 转秒
  assert(/export function buildBudgetPayload|export const buildBudgetPayload/.test(util),
    '导出 buildBudgetPayload 提交体构造函数');
  assert(/\*\s*60/.test(util), '分钟 ×60 转换为 timeout_seconds');
  assert(/timeout_seconds/.test(util) && /max_iterations/.test(util),
    'payload 字段名 timeout_seconds / max_iterations 与 CreateAgentTaskRequest 一致');

  // 默认不传语义：空串不入 payload（保留 Task 1 NULL 回退全局配置）
  assert(/ok:\s*true/.test(util) && /ok:\s*false/.test(util),
    '判别联合返回 ok:true/ok:false');
  assert(/trim\(\)/.test(util) && /!==\s*["']{2}/.test(util),
    '空输入走默认分支（trim 后与空串比较，不构造字段）');

  // 范围外拦截
  assert(/errors/.test(util) && /push/.test(util), '范围外值收集 errors');
}

// ============================================================
// 2. 共享预算表单组件：两个 number 输入 + 范围提示
// ============================================================
console.log('\n[2] components/common/BudgetConfigFields.tsx 表单组件');
const fieldsPath = resolve(srcDir, 'components/common/BudgetConfigFields.tsx');
assert(existsSync(fieldsPath), 'BudgetConfigFields.tsx 共享组件存在');

if (existsSync(fieldsPath)) {
  const fields = readSrc('components/common/BudgetConfigFields.tsx');
  assert(/type=["']number["']/.test(fields), '使用 number 类型输入');
  assert(/超时/.test(fields), '含"超时时间（分钟）"输入项');
  assert(/迭代/.test(fields), '含"最大迭代次数"输入项');
  // 范围提示数字取自 BUDGET_LIMITS 常量（单一真相源），JSX 渲染"范围 x-y"
  assert(/BUDGET_LIMITS/.test(fields) && /范围/.test(fields),
    '展示范围提示（数值取自 BUDGET_LIMITS：120 分钟 / 200 次）');
  assert(/placeholder/.test(fields), '留空走默认的 placeholder 提示');
}

// ============================================================
// 3. 两处创建对话框接线
// ============================================================
const dialogs = [
  ['components/agent/CreateAgentTaskDialog.tsx', 'Agent 页创建对话框'],
  ['components/audit/CreateTaskDialog.tsx', '审计任务列表/项目详情创建对话框'],
];

for (const [rel, label] of dialogs) {
  console.log(`\n[3] ${label}（${rel}）`);
  const src = readSrc(rel);

  assert(/BudgetConfigFields/.test(src), `${label}：引用 BudgetConfigFields 预算表单组件`);
  assert(/buildBudgetPayload/.test(src), `${label}：提交前调用 buildBudgetPayload 构造预算字段`);

  // 提交体携带预算 payload（展开进 createAgentTask 请求体）
  const createCall = src.match(/createAgentTask\(\{[\s\S]*?\}\)/);
  assert(createCall !== null && /\.\.\.[a-zA-Z]*[Bb]udget[a-zA-Z.]*/.test(createCall[0]),
    `${label}：createAgentTask 请求体展开预算 payload`);

  // 范围外拦截：校验失败 toast 且不继续创建
  assert(/ok\s*===\s*false|!.*ok/.test(src) && /toast\.error/.test(src),
    `${label}：预算校验失败时 toast.error 拦截提交`);

  // 对话框打开时重置预算输入（避免上次输入残留）
  assert(/setTimeoutMinutes\(\s*["']{2}\s*\)|setTimeoutMinutes\(["']{2}\)/.test(src)
    || /timeoutMinutes,\s*["']{2}/.test(src),
    `${label}：打开/重置时清空超时输入`);
  assert(/setMaxIterations\(\s*["']{2}\s*\)|setMaxIterations\(["']{2}\)/.test(src)
    || /maxIterations,\s*["']{2}/.test(src),
    `${label}：打开/重置时清空迭代输入`);
}

// audit/CreateTaskDialog 额外约束：预算区块仅 agent 模式显示（快速扫描不经 createAgentTask）
{
  const src = readSrc('components/audit/CreateTaskDialog.tsx');
  const agentModeBlock = src.match(/auditMode\s*===\s*["']agent["'][\s\S]{0,4000}?BudgetConfigFields/);
  assert(agentModeBlock !== null, 'CreateTaskDialog：预算区块仅 agent 审计模式渲染（快速扫描无此预算语义）');
}

// ============================================================
// 4. VerificationResult 类型补 sandbox_skip_reason（Task 8 M2 承接）
// ============================================================
console.log('\n[4] agentTasks.ts VerificationResult 类型承接');
{
  const api = readSrc('shared/api/agentTasks.ts');
  const vrBlock = api.match(/export interface VerificationResult \{[\s\S]*?\}/);
  assert(vrBlock !== null && /sandbox_skip_reason\??:\s*string/.test(vrBlock[0]),
    'VerificationResult 接口声明 sandbox_skip_reason?: string（数据链路 Task 8 已通：落库 verification_result JSON）');
}

// ============================================================
// 5. FindingDetailPanel 展示 skip_reason 中文释义
// ============================================================
console.log('\n[5] FindingDetailPanel skip_reason 展示');
{
  const panel = readSrc('pages/AgentAudit/components/FindingDetailPanel.tsx');
  // 中文释义映射内联于面板（当前唯一消费者）：4 个程序化取值全覆盖
  assert(/gate_release_after_max_redispatch/.test(panel), '映射含 gate_release_after_max_redispatch（R4 达限放行）');
  assert(/orchestrator_max_iterations_exhausted/.test(panel), '映射含 orchestrator_max_iterations_exhausted（轮次耗尽放行）');
  assert(/elastic_exit/.test(panel), '映射含 elastic_exit（弹性退出豁免）');
  assert(/no_poc_template/.test(panel), '映射含 no_poc_template（无 PoC 模板豁免）');
  assert(/function skipReasonLabel|const skipReasonLabel/.test(panel), '面板定义 skipReasonLabel 释义函数');
  assert(/\?\?\s*reason/.test(panel), '未知 reason 回退显示原始字符串');
  assert(/sandbox_skip_reason/.test(panel),
    'FindingDetailPanel 读取 verification_result.sandbox_skip_reason');
  // 条件渲染：有 skip_reason 才展示一行
  assert(/verResult\??\.sandbox_skip_reason\s*&&/.test(panel),
    '有 skip_reason 时条件渲染展示行');
}

// ============================================================
// 汇总
// ============================================================
console.log(`\n${'='.repeat(60)}`);
console.log(`结果: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
