/**
 * Agent 运行预算表单逻辑（sandbox-verification-hard-gate Task 16）
 *
 * 与后端 AgentTaskCreate 约束对齐（backend/app/api/v1/endpoints/agent_tasks.py）：
 * - timeout_seconds: ge=60 le=7200，None 时回退全局 Agent 超时配置（7200s）
 * - max_iterations: ge=1 le=200，后端默认 50
 *
 * 默认值语义裁决：表单留空 = 不传字段，保留 Task 1 "NULL 回退全局配置"语义；
 * 用户显式输入才随请求体提交。
 */

export const BUDGET_LIMITS = {
  /** 超时时间（分钟），×60 后为 timeout_seconds（后端约束 60-7200 秒） */
  timeoutMinutes: { min: 1, max: 120, default: 120 },
  /** 最大迭代次数（后端约束 1-200，默认 50） */
  maxIterations: { min: 1, max: 200, default: 50 },
} as const;

export interface BudgetFormValues {
  timeoutMinutes: string;
  maxIterations: string;
}

export interface BudgetPayload {
  timeout_seconds?: number;
  max_iterations?: number;
}

export type BudgetBuildResult =
  | { ok: true; payload: BudgetPayload }
  | { ok: false; errors: string[] };

/**
 * 由表单字符串构造 createAgentTask 预算字段。
 * 留空字段不进 payload（走后端/全局默认）；范围外或非整数返回错误拦截提交。
 */
export function buildBudgetPayload(values: BudgetFormValues): BudgetBuildResult {
  const errors: string[] = [];
  const payload: BudgetPayload = {};

  const timeoutRaw = values.timeoutMinutes.trim();
  if (timeoutRaw !== "") {
    const minutes = Number(timeoutRaw);
    const { min, max } = BUDGET_LIMITS.timeoutMinutes;
    if (!Number.isInteger(minutes) || minutes < min || minutes > max) {
      errors.push(`超时时间需为 ${min}-${max} 分钟之间的整数`);
    } else {
      payload.timeout_seconds = minutes * 60;
    }
  }

  const iterationsRaw = values.maxIterations.trim();
  if (iterationsRaw !== "") {
    const iterations = Number(iterationsRaw);
    const { min, max } = BUDGET_LIMITS.maxIterations;
    if (!Number.isInteger(iterations) || iterations < min || iterations > max) {
      errors.push(`最大迭代次数需为 ${min}-${max} 之间的整数`);
    } else {
      payload.max_iterations = iterations;
    }
  }

  return errors.length > 0 ? { ok: false, errors } : { ok: true, payload };
}
