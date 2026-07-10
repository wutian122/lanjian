import { Bot, CornerDownLeft, Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { DisabledTooltip } from "@/components/ui/disabled-tooltip";
import { chatWithAgentTask, type AgentTask } from "@/shared/api/agentTasks";
import { apiClient } from "@/shared/api/serverClient";
import { useChat } from "@/shared/context/ChatContext";
import type { AuditTask } from "@/shared/types";

interface ChatWorkspaceProps {
  activeTask: AgentTask | AuditTask | null;
  taskKind: "agent" | "regular" | null;
}

export function ChatWorkspace({ activeTask, taskKind }: ChatWorkspaceProps) {
  const { messages, draft, sending, setMessages, setDraft, setSending } = useChat();

  const canChat = taskKind === "agent" || (!activeTask && true);

  const send = async () => {
    const message = draft.trim();
    if (!message || sending) return;

    setMessages(prev => [...prev, { id: `u-${Date.now()}`, role: "user", content: message }]);
    setDraft("");
    setSending(true);
    try {
      let reply = "";
      if (activeTask && taskKind === "agent") {
        const response = await chatWithAgentTask(activeTask.id, { message });
        reply = response.reply;
      } else {
        const response = await apiClient.post("/agent-tasks/chat/general", { message });
        reply = response.data.reply;
      }
      setMessages(prev => [...prev, { id: `a-${Date.now()}`, role: "assistant", content: reply }]);
    } finally {
      setSending(false);
    }
  };

  const exampleQuestions = activeTask
    ? ["总结当前任务的高危问题", "已验证漏洞的修复建议", "审计进度概况"]
    : ["常见 Web 安全漏洞有哪些", "如何防御 SQL 注入", "代码审计的最佳实践"];

  return (
    <div className="flex h-full min-h-0 flex-col bg-card">
      <div className="border-b border-border px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Bot className="h-4 w-4 text-[#0052ff]" />
            <span className="text-sm font-semibold text-foreground">AI 控制台</span>
          </div>
          <Badge variant="outline">{activeTask ? activeTask.status : "未引用任务"}</Badge>
        </div>
        <div className="mt-2 text-xs text-muted-foreground">
          {activeTask
            ? `当前上下文：${("name" in activeTask && activeTask.name) || "未命名任务"}`
            : "未引用任务，当前为通用 AI 对话模式。"}
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-4 custom-scrollbar bg-muted/20">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center">
            <div className="max-w-md rounded-xl border border-border bg-background p-6 shadow-sm">
              <div className="flex items-center gap-2 mb-3">
                <Bot className="h-5 w-5 text-[#0052ff]" />
                <span className="text-sm font-semibold text-foreground">AI 审计助手</span>
              </div>
              <p className="text-sm text-muted-foreground mb-4">
                {taskKind === "regular"
                  ? "当前引用的是普通审计任务。支持查看上下文和 AI 对话，实时协同仅对 Agent 审计任务开放。"
                  : activeTask
                    ? "当前已引用审计任务，可以直接询问与该任务相关的问题。"
                    : "欢迎进入 AI 审计控制中心。你可以直接询问安全或代码审计相关问题，或从右侧面板引用任务获取上下文。"}
              </p>
              <div className="flex flex-wrap gap-2">
                {exampleQuestions.map((q, i) => (
                  <button
                    key={i}
                    type="button"
                    onClick={() => setDraft(q)}
                    className="rounded-full border border-border bg-muted/50 px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            {messages.map(message => (
              <div
                key={message.id}
                className={message.role === "user"
                  ? "ml-12 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm shadow-sm"
                  : "mr-12 rounded-xl border border-border bg-background px-4 py-3 text-sm shadow-sm border-l-4 border-l-blue-400"
                }
              >
                <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  {message.role === "user" ? "你" : "AI"}
                </div>
                <div className="whitespace-pre-wrap leading-relaxed text-foreground">{message.content}</div>
              </div>
            ))}
            {sending && (
              <div className="mr-12 rounded-xl border border-border bg-background px-4 py-3 text-sm shadow-sm border-l-4 border-l-blue-400">
                <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  AI
                </div>
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span className="text-sm">正在思考...</span>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="border-t border-border bg-card p-4">
        <div className="flex items-end gap-2">
          <Textarea
            value={draft}
            onChange={event => setDraft(event.target.value)}
            placeholder={activeTask ? "例如：总结当前任务的高危问题" : "输入你想了解的安全或代码审计相关问题"}
            className="min-h-[120px] resize-none"
            disabled={!canChat || sending}
          />
          <DisabledTooltip
            show={!canChat || !draft.trim()}
            message={!canChat ? "请先引用一个任务或进入通用对话模式" : "请输入消息内容"}
          >
            <Button className="h-10 shrink-0" onClick={() => void send()} disabled={!canChat || !draft.trim() || sending}>
              <CornerDownLeft className="mr-2 h-4 w-4" />
              发送
            </Button>
          </DisabledTooltip>
        </div>
      </div>
    </div>
  );
}
