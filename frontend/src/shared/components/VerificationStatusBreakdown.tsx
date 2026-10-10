/**
 * Q1: 验证状态分布展示组件
 *
 * 展示 finding 的 verification_status 五类分布：
 * 动态确认(confirmed) / 静态确认(static_confirmed) / 不可复现(not_reproducible) /
 * 待确认(needs_context) / 误报(false_positive)
 *
 * P10-T4（2026-10-09）口径统一：
 * - 「已验证」改为「动态确认」，并显式给出合计（含静态）——
 *   动态确认与静态确认不相交，合计 = 两者之和，防止旧文案「已验证 N」
 *   与「静态确认 M」并列被误读为重复计数（生产实证 84d97d33：
 *   UI「已验证 1」vs 报告「已验证 3」裂缝）。
 * - breakdown 由详情接口实时聚合，历史任务徽标随新口径回溯生效。
 */
import { Badge } from "@/components/ui/badge";

interface VerificationStatusBreakdownProps {
  breakdown?: {
    confirmed: number;
    static_confirmed: number;
    not_reproducible: number;
    needs_context: number;
    false_positive: number;
  };
  className?: string;
  variant?: "compact" | "full";
}

export function VerificationStatusBreakdown({
  breakdown,
  className = "",
  variant = "full",
}: VerificationStatusBreakdownProps) {
  if (!breakdown) {
    return null;
  }

  const {
    confirmed,
    static_confirmed,
    not_reproducible,
    needs_context,
    false_positive,
  } = breakdown;
  // 合计（含静态）：动态确认与静态确认不相交
  const totalVerified = confirmed + static_confirmed;

  if (variant === "compact") {
    return (
      <div className={`text-xs text-muted-foreground ${className}`}>
        动态确认 {confirmed} · 合计 {totalVerified}（含静态） / 不可复现 {not_reproducible} / 待确认 {needs_context} / 误报 {false_positive}
      </div>
    );
  }

  return (
    <div className={`flex flex-wrap gap-1.5 ${className}`}>
      <Badge variant="outline" className="text-[10px] text-green-600 border-green-200">
        动态确认 {confirmed}
      </Badge>
      <Badge variant="outline" className="text-[10px] text-emerald-700 border-emerald-300">
        合计 {totalVerified}（含静态）
      </Badge>
      <Badge variant="outline" className="text-[10px] text-blue-600 border-blue-200">
        静态确认 {static_confirmed}
      </Badge>
      <Badge variant="outline" className="text-[10px] text-yellow-600 border-yellow-200">
        不可复现 {not_reproducible}
      </Badge>
      <Badge variant="outline" className="text-[10px] text-blue-600 border-blue-200">
        待确认 {needs_context}
      </Badge>
      <Badge variant="outline" className="text-[10px] text-red-600 border-red-200">
        误报 {false_positive}
      </Badge>
    </div>
  );
}
