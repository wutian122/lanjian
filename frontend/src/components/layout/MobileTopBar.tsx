import { Menu } from "lucide-react";

import { Button } from "@/components/ui/button";

interface MobileTopBarProps {
  title: string;
  onMenuClick: () => void;
}

export function MobileTopBar({ title, onMenuClick }: MobileTopBarProps) {
  return (
    <div className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-border bg-background/95 px-4 backdrop-blur md:hidden">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        aria-label="打开导航菜单"
        onClick={onMenuClick}
      >
        <Menu className="h-5 w-5" />
      </Button>
      <div className="min-w-0 flex-1">
        <p className="truncate text-base font-semibold text-foreground">{title}</p>
      </div>
    </div>
  );
}
