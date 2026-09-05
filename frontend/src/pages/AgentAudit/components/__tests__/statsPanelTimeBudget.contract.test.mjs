/**
 * Task 17 静态契约测试：详情页 StatsPanel 时间预算/剩余时间格
 *
 * 前端无 vitest/jest 基建（离线环境），沿用仓库零依赖静态断言先例
 * （budgetConfig.contract.test.mjs / sandboxEvidenceMarkers.contract.test.mjs：
 * 读源码断言契约 + Node 原生 TS 类型剥离直跑 .ts 行为），直接用 node 运行：
 *   node frontend/src/pages/AgentAudit/components/__tests__/statsPanelTimeBudget.contract.test.mjs
 *
 * 覆盖 openspec 变更 sandbox-verification-hard-gate Task 17
 * （design.md:59——StatsPanel「时间预算」格：运行中=剩余、完成态=耗时）：
 * - shared/utils/timeBudget.ts 纯逻辑：
 *   运行态 → 剩余时间 = started_at + timeout_seconds - now（mm:ss，超时 clamp 00:00）
 *   终态   → 已用时间 = completed_at - started_at
 *   pending/paused/数据缺失 → 不显示（null）
 * - StatsPanel.tsx：条件渲染时间格 + 1s tick（setInterval 1000 + clearInterval）
 * - AgentTask 类型补 timeout_seconds?: number | null（详情接口契约）
 * - 后端 AgentTaskResponse 回传 timeout_seconds，且取值复用
 *   resolve_task_timeout_seconds（与 watchdog/orchestrator deadline 同源回退链）
 */
import { readFileSync, existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
// frontend/src/pages/AgentAudit/components/__tests__ → frontend/src
const feSrcDir = resolve(__dirname, '..', '..', '..', '..');
// frontend/src → 仓库根
const repoRoot = resolve(feSrcDir, '..', '..');

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

function readFe(rel) {
  return readFileSync(resolve(feSrcDir, rel), 'utf-8');
}

// 中文文案断言：源码可能直接书写中文或以  转义存储，两种形式运行时语义等价。
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
// 1. timeBudget 纯逻辑模块：运行时行为（Node 原生 TS 剥离直跑）
// ============================================================
console.log('\n[1] shared/utils/timeBudget.ts describeTimeBudget 运行时行为');
const mod = await import(resolve(feSrcDir, 'shared/utils/timeBudget.ts')).catch(() => null);
const describeTimeBudget = mod?.describeTimeBudget;
const formatElapsed = mod?.formatElapsed;

assert(typeof describeTimeBudget === 'function', '导出 describeTimeBudget 函数');
assert(typeof formatElapsed === 'function', '导出 formatElapsed 格式化函数');

if (typeof describeTimeBudget === 'function') {
  // 运行态：started_at + timeout_seconds 与当前时间差 → 剩余 mm:ss
  {
    // now 固定为 2026-09-05T12:00:30Z；任务 12:00:00Z 启动，预算 120s → 剩 90s
    const now = Date.parse('2026-09-05T12:00:30Z');
    const view = describeTimeBudget(
      {
        status: 'running',
        started_at: '2026-09-05T20:00:00+08:00', // 北京时间 = 12:00:00Z（后端 serialize_cst 形态）
        completed_at: null,
        timeout_seconds: 120,
      },
      now,
    );
    assert(view !== null, '运行态返回时间格视图（非 null）');
    assert(view && zhRe('剩余时间').test(view.label), '运行态标签为「剩余时间」');
    assert(view && view.value === '01:30', `运行态剩余 90s → 01:30（实得 ${view?.value}）`);
    assert(view && view.tone === 'running', '未超时 tone=running');
  }

  // 运行态超时（wall clock 过 deadline，watchdog 宽限/收尾窗口内仍在跑）→ clamp 00:00
  {
    const now = Date.parse('2026-09-05T12:05:00Z'); // 已超预算 5 分钟
    const view = describeTimeBudget(
      {
        status: 'verifying',
        started_at: '2026-09-05T12:00:00Z',
        completed_at: null,
        timeout_seconds: 120,
      },
      now,
    );
    assert(view !== null, '超时仍在跑仍返回视图');
    assert(view && view.value === '00:00', `超时剩余 clamp 00:00（实得 ${view?.value}，不显示负数）`);
    assert(view && view.tone === 'overdue', '超时 tone=overdue（可供 StatsPanel 变红警示）');
  }

  // 终态：completed_at - started_at → 已用时间
  {
    const view = describeTimeBudget({
      status: 'completed',
      started_at: '2026-09-05T12:00:00Z',
      completed_at: '2026-09-05T12:07:05Z', // 7 分 5 秒 = 425s
      timeout_seconds: 7200,
    });
    assert(view !== null, '完成态返回视图');
    assert(view && zhRe('已用时间').test(view.label), '完成态标签为「已用时间」');
    assert(view && view.value === '07:05', `完成态耗时 425s → 07:05（实得 ${view?.value}）`);
    assert(view && view.tone === 'done', '完成态 tone=done');
  }

  // completed_with_gaps / failed / cancelled 同为终态耗时
  {
    const view = describeTimeBudget({
      status: 'failed',
      started_at: '2026-09-05T12:00:00Z',
      completed_at: '2026-09-05T12:00:45Z',
      timeout_seconds: 1800,
    });
    assert(view && zhRe('已用时间').test(view.label) && view.value === '00:45',
      'failed 终态同样展示已用时间 00:45');
  }

  // pending（未启动，started_at=null）→ null（不显示格子）
  {
    const view = describeTimeBudget({
      status: 'pending',
      started_at: null,
      completed_at: null,
      timeout_seconds: 3600,
    });
    assert(view === null, 'pending 未启动 → null（不显示时间格）');
  }

  // paused → null（wall-clock 剩余含暂停时长会误导，暂停态不展示倒计时）
  {
    const view = describeTimeBudget({
      status: 'paused',
      started_at: '2026-09-05T12:00:00Z',
      completed_at: null,
      timeout_seconds: 3600,
    });
    assert(view === null, 'paused 暂停态 → null（不展示倒计时）');
  }

  // 运行态但 timeout_seconds 缺失（防御：后端回退链失效时）→ null
  {
    const view = describeTimeBudget(
      {
        status: 'running',
        started_at: '2026-09-05T12:00:00Z',
        completed_at: null,
        timeout_seconds: null,
      },
      Date.parse('2026-09-05T12:00:30Z'),
    );
    assert(view === null, '运行态无 timeout_seconds → null（不臆造预算）');
  }

  // 终态但缺 completed_at/started_at → null
  {
    const view = describeTimeBudget({
      status: 'completed',
      started_at: null,
      completed_at: null,
      timeout_seconds: 3600,
    });
    assert(view === null, '终态缺时间戳 → null');
  }

  // 超 1 小时格式 h:mm:ss（7200s 预算必然跨小时）
  {
    assert(typeof formatElapsed === 'function' && formatElapsed(3725) === '1:02:05',
      `formatElapsed(3725) → 1:02:05（实得 ${typeof formatElapsed === 'function' ? formatElapsed(3725) : 'N/A'}）`);
    assert(typeof formatElapsed === 'function' && formatElapsed(75) === '01:15',
      'formatElapsed(75) → 01:15（分钟内 mm:ss 补零）');
    assert(typeof formatElapsed === 'function' && formatElapsed(0) === '00:00',
      'formatElapsed(0) → 00:00');
  }
}

// ============================================================
// 2. AgentTask 类型补 timeout_seconds 字段（详情接口契约）
// ============================================================
console.log('\n[2] shared/api/agentTasks.ts AgentTask 类型字段');
{
  const api = readFe('shared/api/agentTasks.ts');
  const block = api.match(/export interface AgentTask \{[\s\S]*?\n\}/);
  assert(block !== null, 'AgentTask 接口存在');
  if (block) {
    assert(/timeout_seconds\??:\s*(number\s*\|\s*null|number)/.test(block[0]),
      'AgentTask.timeout_seconds?: number | null（详情接口回传有效预算秒数）');
    // started_at/completed_at 既有字段（时间格依赖）
    assert(/started_at\??:\s*string\s*\|\s*null/.test(block[0]),
      'AgentTask.started_at: string | null（剩余/耗时计算起点）');
    assert(/completed_at\??:\s*string\s*\|\s*null/.test(block[0]),
      'AgentTask.completed_at: string | null（终态耗时终点）');
  }
}

// ============================================================
// 3. StatsPanel.tsx 时间格渲染分支
// ============================================================
console.log('\n[3] StatsPanel.tsx 时间预算格');
const statsPath = 'pages/AgentAudit/components/StatsPanel.tsx';
{
  assert(existsSync(resolve(feSrcDir, statsPath)), 'StatsPanel.tsx 存在');
  const sp = readFe(statsPath);

  // 接线：导入并调用 describeTimeBudget
  assert(/describeTimeBudget/.test(sp), 'StatsPanel 接线 describeTimeBudget');
  assert(/from\s+["']@\/shared\/utils\/timeBudget["']/.test(sp),
    '从 @/shared/utils/timeBudget 导入');

  // 1s tick：运行中倒计时每秒刷新；卸载清理
  assert(/setInterval\([\s\S]*?1000\)/.test(sp), '1s tick（setInterval 1000ms）驱动倒计时刷新');
  assert(/clearInterval/.test(sp), 'tick 定时器卸载时 clearInterval 清理');

  // 条件渲染：view 为 null（pending/paused/缺数据）时不出现格子
  assert(/timeBudget\s*&&/.test(sp) || /budgetView\s*&&/.test(sp) || /&&\s*\(\s*<MetricItem/.test(sp),
    '时间格条件渲染（视图为 null 不渲染）');

  // 标签与值来自视图（剩余时间/已用时间 + mm:ss）
  assert(zhRe('剩余时间').test(sp) || /view\.label|budget\.label|timeBudget\.label/.test(sp),
    '渲染剩余时间标签（view.label 或内联文案）');
  assert(/view\.value|budget\.value|timeBudget\.value/.test(sp),
    '渲染时间值 view.value（mm:ss / h:mm:ss）');

  // 超时变红：tone=overdue 有红色样式分支
  assert(/overdue[\s\S]{0,300}?text-red-|text-red-[\s\S]{0,300}?overdue/.test(sp),
    'tone=overdue 超时红色警示样式');

  // MetricItem 格子接入（复用既有指标格组件）
  assert(/<MetricItem/.test(sp), '时间格复用 MetricItem 指标格组件');
}

// ============================================================
// 4. 后端详情接口回传 timeout_seconds（与 watchdog 同源解析）
// ============================================================
console.log('\n[4] 后端 AgentTaskResponse 回传有效预算');
{
  const ep = readFileSync(
    resolve(repoRoot, 'backend/app/api/v1/endpoints/agent_tasks.py'),
    'utf-8',
  );

  // 响应模型含字段
  const respBlock = ep.match(/class AgentTaskResponse\(BaseModel\):[\s\S]*?class Config:/);
  assert(respBlock !== null && /timeout_seconds\s*:/.test(respBlock[0]),
    'AgentTaskResponse 声明 timeout_seconds 字段');

  // 详情端点 response_data 填充该字段（字典字面量或赋值形态均接受）
  const detailBlock = ep.slice(ep.indexOf('async def get_agent_task('));
  assert(/response_data\[["']timeout_seconds["']\]\s*=|"timeout_seconds"\s*:/.test(detailBlock),
    'get_agent_task response_data 填充 timeout_seconds');

  // 同源：复用 resolve_task_timeout_seconds（显式值 > llmConfig.agentTimeout > 全局 1800），
  // 不臆造字面量——NULL 任务必须回退与 watchdog deadline 同一时钟
  assert(/resolve_task_timeout_seconds\(/.test(detailBlock),
    '详情端点复用 resolve_task_timeout_seconds（与 watchdog/orchestrator deadline 同源）');
  assert(/_get_user_config\(/.test(detailBlock),
    '详情端点取 user_config 供回退链解析（llmConfig.agentTimeout）');
}

// ============================================================
// 汇总
// ============================================================
console.log(`\n${'='.repeat(60)}`);
console.log(`结果: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
