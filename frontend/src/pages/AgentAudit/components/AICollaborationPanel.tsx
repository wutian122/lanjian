import { useState } from "react";
import { Bot, CornerDownLeft, RotateCcw, Sparkles, Terminal, Wrench } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useLocalStorage } from "@/shared/hooks/useLocalStorage";

import type { AICollaborationPanelProps } from "../types";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

export function AICollaborationPanel({
  context,
  isRunning,
  onSendMessage,
  onRequestContinueAudit,
  onRequestRerunPoc,
  taskId,
}: AICollaborationPanelProps) {
  const [messages, setMessages] = useLocalStorage<ChatMessage[]>(`ai-chat-${taskId}`, []);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);

  const quickActions = [
    {
      label: "继续审计",
      icon: Sparkles,
      action: async () => {
        onRequestContinueAudit();
        await sendMessage("请基于当前任务上下文继续推进审计，并优先解释下一步高价值动作。", false);
      },
    },
    {
      label: "解释日志",
      icon: Terminal,
      action: async () => {
        await sendMessage("请根据最近日志解释当前任务状态、阻塞点和下一步建议。", false);
      },
    },
    {
      label: "重跑 PoC",
      icon: RotateCcw,
      action: async () => {
        const firstPoc = context.pocResults[0];
        if (firstPoc) {
          onRequestRerunPoc(firstPoc.findingId);
          await sendMessage(`请重新验证漏洞 ${firstPoc.title} 的 PoC，并说明复测重点。`, false);
        }
      },
    },
  ];

  async function sendMessage(message: string, clearDraft = true) {
    const trimmed = message.trim();
    if (!trimmed || isSending) return;

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: trimmed,
    };

    setMessages(prev => [...prev, userMessage]);
    if (clearDraft) {
      setDraft("");
    }
    setIsSending(true);

    try {
      const reply = await onSendMessage(trimmed);
      setMessages(prev => [
        ...prev,
        {
          id: `assistant-${Date.now()}`,
          role: "assistant",
          content: reply,
        },
      ]);
    } finally {
      setIsSending(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-card">
      <div className="flex-shrink-0 border-b border-border px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <Bot className="h-4 w-4 text-[#0052ff]" />
            <span className="text-sm font-bold uppercase tracking-wider text-foreground">AI 协同审计</span>
          </div>
          <Badge variant="outline" className="border-blue-200 bg-blue-50 text-xs text-blue-700">
            Context Ready
          </Badge>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          自动汇总任务、漏洞、日志、工具调用和 PoC 摘要，作为当前工作台的协同上下文。
        </p>
      </div>

      <div className="flex-shrink-0 border-b border-border bg-background/80 px-4 py-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">当前上下文</span>
          <Badge variant="outline" className="text-xs font-mono">
            {context.taskStatus}
          </Badge>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
          <div className="rounded-md border border-border bg-card px-2.5 py-2">
            <div>任务</div>
            <div className="mt-1 truncate text-foreground">{context.taskName}</div>
          </div>
          <div className="rounded-md border border-border bg-card px-2.5 py-2">
            <div>阶段</div>
            <div className="mt-1 text-foreground">{context.currentPhase}</div>
          </div>
          <div className="rounded-md border border-border bg-card px-2.5 py-2">
            <div>进度</div>
            <div className="mt-1 text-[#0052ff]">{context.progressPercentage.toFixed(0)}%</div>
          </div>
          <div className="rounded-md border border-border bg-card px-2.5 py-2">
            <div>验证</div>
            <div className="mt-1 text-foreground">{context.verifiedFindings} 个已验证</div>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          {quickActions.map(item => {
            const Icon = item.icon;
            return (
              <Button
                key={item.label}
                size="sm"
                variant="outline"
                className="h-auto gap-1.5 px-2.5 py-2 text-xs"
                onClick={() => void item.action()}
                disabled={item.label === "重跑 PoC" && context.pocResults.length === 0}
              >
                <Icon className="h-3.5 w-3.5" />
                {item.label}
              </Button>
            );
          })}
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto space-y-3 p-4 custom-scrollbar bg-muted/20">
        {messages.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border bg-background p-4 text-sm text-muted-foreground">
            <div className="mb-2 flex items-center gap-2 text-foreground">
              <Wrench className="h-4 w-4 text-[#0052ff]" />
              <span className="font-medium">协同建议</span>
            </div>
            <ul className="space-y-1 leading-relaxed">
              <li>1. 让 AI 解释最近日志和 Agent 行为</li>
              <li>2. 让 AI 深挖高危漏洞或利用链</li>
              <li>3. 让 AI 给出下一步工具调用建议</li>
            </ul>
          </div>
        ) : (
          messages.map(message => (
            <div
              key={message.id}
              className={message.role === "user"
                ? "ml-6 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-slate-800"
                : "mr-6 rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"
              }
            >
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                {message.role === "user" ? "你" : "AI"}
              </div>
              <div className="whitespace-pre-wrap leading-relaxed">{message.content}</div>
            </div>
          ))
        )}
      </div>

      <div className="flex-shrink-0 border-t border-border bg-card p-4">
        <div className="mb-2 text-xs text-muted-foreground">
          {isRunning ? "任务运行中，可继续发起协同提问。" : "任务已结束，可继续复盘和追问结果。"}
        </div>
        <div className="flex items-end gap-2">
          <Textarea
            value={draft}
            onChange={event => setDraft(event.target.value)}
            placeholder="例如：解释最近日志、继续审计这个任务、重新验证某个 PoC"
            className="min-h-[84px] resize-none"
          />
          <Button
            className="h-10 shrink-0"
            onClick={() => void sendMessage(draft)}
            disabled={!draft.trim() || isSending}
          >
            <CornerDownLeft className="mr-2 h-4 w-4" />
            发送
          </Button>
        </div>
      </div>
    </div>
  );
}
