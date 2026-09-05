/**
 * Agent 运行预算表单字段（Task 16）
 *
 * 两处创建对话框（agent/CreateAgentTaskDialog、audit/CreateTaskDialog 的 agent
 * 模式）高级选项区共用。留空 = 不传字段（超时回退全局 llmConfig.agentTimeout，默认 1800s；
 * 迭代走后端默认 50），校验逻辑见 shared/utils/budgetConfig.ts。
 */
import { Timer, Repeat } from "lucide-react";
import { Input } from "@/components/ui/input";
import { BUDGET_LIMITS } from "@/shared/utils/budgetConfig";

interface BudgetConfigFieldsProps {
  timeoutMinutes: string;
  maxIterations: string;
  onTimeoutMinutesChange: (value: string) => void;
  onMaxIterationsChange: (value: string) => void;
}

export function BudgetConfigFields({
  timeoutMinutes,
  maxIterations,
  onTimeoutMinutesChange,
  onMaxIterationsChange,
}: BudgetConfigFieldsProps) {
  const t = BUDGET_LIMITS.timeoutMinutes;
  const i = BUDGET_LIMITS.maxIterations;

  return (
    <div className="p-3 border border-dashed border-border rounded bg-muted/50 space-y-3">
      <span className="text-xs font-semibold text-muted-foreground">
        运行预算
      </span>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <label className="flex items-center gap-1 text-xs text-muted-foreground">
            <Timer className="w-3 h-3" />
            超时时间（分钟）
          </label>
          <Input
            type="number"
            min={t.min}
            max={t.max}
            step={1}
            value={timeoutMinutes}
            onChange={(e) => onTimeoutMinutesChange(e.target.value)}
            placeholder="留空使用全局超时配置"
            className="h-8 text-sm"
          />
          <p className="text-[11px] text-muted-foreground">
            范围 {t.min}-{t.max} 分钟
          </p>
        </div>

        <div className="space-y-1">
          <label className="flex items-center gap-1 text-xs text-muted-foreground">
            <Repeat className="w-3 h-3" />
            最大迭代次数
          </label>
          <Input
            type="number"
            min={i.min}
            max={i.max}
            step={1}
            value={maxIterations}
            onChange={(e) => onMaxIterationsChange(e.target.value)}
            placeholder={`默认 ${i.default}`}
            className="h-8 text-sm"
          />
          <p className="text-[11px] text-muted-foreground">
            范围 {i.min}-{i.max} 次
          </p>
        </div>
      </div>

      <p className="text-[11px] text-muted-foreground">
        留空使用默认配置；超时时间到达后任务自动收口，迭代次数为 Agent 最大执行轮次。
      </p>
    </div>
  );
}
