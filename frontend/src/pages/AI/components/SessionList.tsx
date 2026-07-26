import { MessageSquarePlus } from "lucide-react";

import { Button } from "@/components/ui/button";

export type AISession = {
  id: string;
  name: string;
  taskKind?: "agent" | "regular";
  referencedTaskId?: string;
  updatedAt: string;
};

interface SessionListProps {
  sessions: AISession[];
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void;
  onCreate: () => void;
}

export function SessionList({ sessions, activeSessionId, onSelect, onCreate }: SessionListProps) {
  return (
    <div className="flex h-full min-h-0 flex-col border-r border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div>
          <div className="text-sm font-semibold text-foreground">会话列表</div>
          <div className="text-xs text-muted-foreground">多会话审计协同入口</div>
        </div>
        <Button size="sm" variant="outline" onClick={onCreate}>
          <MessageSquarePlus className="mr-2 h-4 w-4" />
          新会话
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 custom-scrollbar">
        <div className="space-y-2">
          {sessions.map(session => {
            const isActive = session.id === activeSessionId;
            return (
              <button
                key={session.id}
                type="button"
                onClick={() => onSelect(session.id)}
                className={[
                  "w-full rounded-lg border px-3 py-3 text-left transition-colors",
                  isActive
                    ? "border-blue-200 bg-blue-50"
                    : "border-border bg-background hover:bg-muted/50",
                ].join(" ")}
              >
                <div className="text-sm font-medium text-foreground">{session.name}</div>
                <div className="mt-1 text-xs text-muted-foreground">{session.updatedAt}</div>
                {session.referencedTaskId && (
                  <div className="mt-2 text-xs text-[#0052ff]">引用任务：{session.referencedTaskId.slice(0, 8)}...</div>
                )}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
