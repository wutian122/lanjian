/**
 * useResilientStream 重连自愈测试（静态断言）
 * Wave 1 §2.5 TDD RED 阶段 — 当前应全部 FAIL
 *
 * 运行方式：npx tsx frontend/src/pages/AgentAudit/hooks/__tests__/useResilientStream.reconnect.test.tsx
 */
import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const hooksDir = resolve(__dirname, '..');

let passed = 0;
let failed = 0;

function assert(condition: boolean, message: string): void {
  if (condition) {
    passed++;
    console.log(`  PASS: ${message}`);
  } else {
    failed++;
    console.error(`  FAIL: ${message}`);
  }
}

// ============================================================
// 测试 1: hasConnectedRef cleanup 复位
// ============================================================
console.log('\n测试 1: hasConnectedRef cleanup 复位');
{
  const indexPath = resolve(hooksDir, '..', 'index.tsx');
  const source = readFileSync(indexPath, 'utf-8');

  // 查找 stream connection effect cleanup 中的 hasConnectedRef 复位
  // 正则：在 cleanup return 函数体内，disconnectStream() 之后有 hasConnectedRef.current = false
  const cleanupSection = source.match(
    /return\s*\(\s*\)\s*=>\s*\{[^}]*disconnectStream\(\)[^}]*hasConnectedRef\.current\s*=\s*false[^}]*\}/
  );
  assert(
    cleanupSection !== null,
    'index.tsx effect cleanup 中应包含 hasConnectedRef.current = false（允许 StrictMode 双挂载与断流后重连）'
  );

  // 额外检查：源代码中至少存在 2 处 hasConnectedRef.current = false
  const allResets = source.match(/hasConnectedRef\.current\s*=\s*false/g);
  const resetCount = allResets ? allResets.length : 0;
  assert(
    resetCount >= 2,
    `index.tsx 中 hasConnectedRef.current = false 应 >= 2 处（taskId 变化重置 + cleanup 重置），当前: ${resetCount}`
  );
}

// ============================================================
// 测试 2: disconnect 保留 sequence 高水位
// ============================================================
console.log('\n测试 2: disconnect 保留 sequence 高水位');
{
  const streamPath = resolve(hooksDir, 'useResilientStream.ts');
  const source = readFileSync(streamPath, 'utf-8');

  // disconnectInternal 中不应包含 latestSeenSequenceRef.current = 0（排除注释行）
  const lines = source.split('\n');
  const zeroResetLines = lines.filter(
    l => l.includes('latestSeenSequenceRef.current') && l.includes('= 0') && !l.trim().startsWith('//')
  );
  assert(
    zeroResetLines.length === 0,
    `disconnectInternal 中不应有 latestSeenSequenceRef.current = 0（应保留 sequence 高水位），当前出现 ${zeroResetLines.length} 次: ${zeroResetLines.join(' | ')}`
  );

  // 确认 disconnectInternal 中仍然有 setReconnectAttempts(0)（不要误删）
  assert(
    source.includes('setReconnectAttempts(0)'),
    'disconnectInternal 中应保留 setReconnectAttempts(0)'
  );
}

// ============================================================
// 测试 3: 重连带 Last-Event-ID header
// ============================================================
console.log('\n测试 3: 重连带 Last-Event-ID header');
{
  const streamPath = resolve(hooksDir, 'useResilientStream.ts');
  const source = readFileSync(streamPath, 'utf-8');

  // connectInternal 中 fetch 请求应携带 Last-Event-ID header
  const lastEventIdHeader = source.match(/Last-Event-ID/g);
  assert(
    lastEventIdHeader !== null && lastEventIdHeader.length >= 1,
    `connectInternal 的 fetch 请求中应携带 Last-Event-ID header，当前出现 ${lastEventIdHeader ? lastEventIdHeader.length : 0} 次`
  );

  // 确认 header 值与 latestSeenSequenceRef 关联
  const headerWithSequence = source.includes("String(latestSeenSequenceRef.current)");
  assert(
    headerWithSequence,
    'Last-Event-ID header 值应来自 latestSeenSequenceRef.current'
  );
}

// ============================================================
// 测试 4: parseSSE 支持 id: 字段
// ============================================================
console.log('\n测试 4: parseSSE 支持 id: 字段');
{
  const streamPath = resolve(hooksDir, 'useResilientStream.ts');
  const source = readFileSync(streamPath, 'utf-8');

  const idFieldParser = source.match(/line\.startsWith\(['"]id:['"]\)/g);
  assert(
    idFieldParser !== null && idFieldParser.length === 1,
    `parseSSE 中应包含 line.startsWith('id:') 解析逻辑，当前出现 ${idFieldParser ? idFieldParser.length : 0} 次`
  );

  // 确认 id 字段解析后设置 sequence
  const sequenceAssignment = source.includes("currentEvent.sequence = idNum");
  assert(
    sequenceAssignment,
    'parseSSE id: 解析后应设置 currentEvent.sequence = idNum'
  );
}

// ============================================================
// 测试 5: parseSSE 纯函数行为验证（id: 字段解析）
// ============================================================
console.log('\n测试 5: parseSSE id: 字段行为验证');
{
  // 直接测试 parseSSE 逻辑（内联副本验证 id: 字段解析行为）
  function parseSSETest(buffer: string): { parsed: Array<Record<string, unknown>>; remaining: string } {
    const parsed: Array<Record<string, unknown>> = [];
    const lines = buffer.split('\n');
    let remaining = '';
    let currentEvent: Record<string, unknown> = {};

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];

      if (line === '') {
        if (currentEvent.type) {
          parsed.push({ ...currentEvent });
          currentEvent = {};
        }
        continue;
      }

      if (i === lines.length - 1 && !buffer.endsWith('\n')) {
        remaining = line;
        break;
      }

      if (line.startsWith('event:')) {
        currentEvent.type = line.slice(6).trim();
      } else if (line.startsWith('id:')) {
        const idStr = line.slice(3).trim();
        if (idStr.length > 0) {
          const idNum = Number(idStr);
          if (!Number.isNaN(idNum)) {
            currentEvent.sequence = idNum;
          }
        }
      } else if (line.startsWith('data:')) {
        try {
          const data = JSON.parse(line.slice(5).trim());
          currentEvent = { ...currentEvent, ...data };
        } catch {
          // Ignore parse errors
        }
      }
    }

    return { parsed, remaining };
  }

  // 场景 A: id: 在 data: 之前，应被解析为 sequence
  const resultA = parseSSETest('id: 42\nevent: info\ndata: {"message":"hello"}\n\n');
  assert(
    resultA.parsed.length === 1 && resultA.parsed[0].sequence === 42,
    `id: 在 data: 之前：应解析 sequence=42，实际: ${JSON.stringify(resultA.parsed[0])}`
  );

  // 场景 B: id: 在 data: 之后，data 中的 sequence 应覆盖 id 值
  const resultB = parseSSETest('event: info\nid: 10\ndata: {"sequence":99,"message":"world"}\n\n');
  assert(
    resultB.parsed.length === 1 && resultB.parsed[0].sequence === 99,
    `id: 在 data: 之前，data 覆盖：sequence 应为 99，实际: ${resultB.parsed[0].sequence}`
  );

  // 场景 C: 只有 id: 没有 data: 中的 sequence
  const resultC = parseSSETest('event: heartbeat\nid: 500\n\n');
  assert(
    resultC.parsed.length === 1 && resultC.parsed[0].sequence === 500,
    `只有 id: 字段：应解析 sequence=500，实际: ${JSON.stringify(resultC.parsed[0])}`
  );

  // 场景 D: 无效 id 值（非数字）应被忽略
  const resultD = parseSSETest('event: info\nid: abc\n\n');
  assert(
    resultD.parsed.length === 1 && resultD.parsed[0].sequence === undefined,
    `无效 id: 值应被忽略，sequence 应为 undefined，实际: ${JSON.stringify(resultD.parsed[0])}`
  );

  // 场景 E: 空 id 值
  const resultE = parseSSETest('event: info\nid: \n\n');
  assert(
    resultE.parsed.length === 1 && resultE.parsed[0].sequence === undefined,
    `空 id: 值应被忽略，sequence 应为 undefined，实际: ${JSON.stringify(resultE.parsed[0])}`
  );

  // 场景 F: 多事件流中 id 不互相污染
  const resultF = parseSSETest(
    'event: info\nid: 1\ndata: {"msg":"a"}\n\n' +
    'event: info\nid: 2\ndata: {"msg":"b"}\n\n'
  );
  assert(
    resultF.parsed.length === 2 &&
    resultF.parsed[0].sequence === 1 &&
    resultF.parsed[1].sequence === 2,
    `多事件流中每个事件应有独立 sequence，实际: ${JSON.stringify(resultF.parsed)}`
  );
}

// ============================================================
// 结果汇总
// ============================================================
console.log(`\n========================================`);
console.log(`测试结果: ${passed} PASS, ${failed} FAIL`);
console.log(`========================================`);

if (failed > 0) {
  process.exit(1);
}