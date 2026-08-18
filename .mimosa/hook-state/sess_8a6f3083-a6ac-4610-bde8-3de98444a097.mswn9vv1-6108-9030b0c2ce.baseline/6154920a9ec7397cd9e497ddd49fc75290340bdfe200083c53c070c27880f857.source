import type { ReactNode } from "react";

interface DisabledTooltipProps {
  /** 按钮禁用条件为 true 时显示提示 */
  show: boolean;
  /** 提示文本 */
  message: string;
  /** 子元素（通常是 Button） */
  children: ReactNode;
}

export function DisabledTooltip({ show, message, children }: DisabledTooltipProps) {
  if (!show) return <>{children}</>;

  return (
    <div className="relative group">
      {children}
      <div className="absolute top-full mt-1.5 left-1/2 -translate-x-1/2 bg-popover text-popover-foreground text-xs px-3 py-1.5 rounded-md shadow-md border border-border whitespace-nowrap z-50 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
        {message}
      </div>
    </div>
  );
}
