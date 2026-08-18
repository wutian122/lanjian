/**
 * Q1: 验证状态分布展示组件
 *
 * 展示 finding 的 verification_status 四类分布：
 * 已验证(confirmed) / 不可复现(not_reproducible) / 待确认(needs_context) / 误报(false_positive)
 *
 * 保留 verified_count 严格语义（仅 confirmed），同时展示其余状态，避免用户误解。
 */
import { Badge } from "@/components/ui/badge";

interface VerificationStatusBreakdownProps {
  breakdown?: {
    confirmed: number;
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

  const { confirmed, not_reproducible, needs_context, false_positive } = breakdown;

  if (variant === "compact") {
    return (
      <div className={`text-xs text-muted-foreground ${className}`}>
        已验证 {confirmed} / 不可复现 {not_reproducible} / 待确认 {needs_context} / 误报 {false_positive}
      </div>
    );
  }

  return (
    <div className={`flex flex-wrap gap-1.5 ${className}`}>
      <Badge variant="outline" className="text-[10px] text-green-600 border-green-200">
        已验证 {confirmed}
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
