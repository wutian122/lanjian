/**
 * Agent Detail Panel Component
 * Clean enterprise agent information panel
 */

import { memo } from "react";
import { X, Cpu, Scan, FileSearch, ShieldCheck, Bot, Repeat, Zap, Bug, FileCode, Clock, Network } from "lucide-react";
import { cn } from "@/shared/utils/utils";
import { AGENT_STATUS_CONFIG } from "../constants";
import { findAgentInTree } from "../utils";
import type { AgentDetailPanelProps } from "../types";

const AGENT_TYPE_CONFIG: Record<string, { icon: React.ReactNode; label: string }> = {
  orchestrator: { icon: <Cpu className="w-4 h-4 text-violet-600" />, label: "编排器" },
  recon: { icon: <Scan className="w-4 h-4 text-teal-600" />, label: "侦察" },
  analysis: { icon: <FileSearch className="w-4 h-4 text-amber-600" />, label: "分析" },
  verification: { icon: <ShieldCheck className="w-4 h-4 text-emerald-600" />, label: "验证" },
};

export const AgentDetailPanel = memo(function AgentDetailPanel({ agentId, treeNodes, onClose }: AgentDetailPanelProps) {
  const agent = findAgentInTree(treeNodes, agentId);
  if (!agent) return null;

  const statusConfig = AGENT_STATUS_CONFIG[agent.status] || AGENT_STATUS_CONFIG.created;
  const typeConfig = AGENT_TYPE_CONFIG[agent.agent_type] || {
    icon: <Bot className="w-4 h-4 text-muted-foreground" />,
    label: "Agent",
  };

  const isRunning = agent.status === 'running';

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-border bg-muted/30">
        <div className="flex items-center gap-2.5">
          <div className="p-1.5 rounded-md bg-background border border-border">
            {typeConfig.icon}
          </div>
          <div>
            <span className="text-sm font-medium text-foreground block">{agent.agent_name}</span>
            <span className="text-xs text-muted-foreground">{typeConfig.label}</span>
          </div>
        </div>

        <button
          onClick={onClose}
          className="w-6 h-6 flex items-center justify-center rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Status indicator */}
      <div className="px-3 py-2 border-b border-border bg-muted/20">
        <div className="flex items-center gap-2">
          <div className="relative">
            <div className={cn(
              "w-2.5 h-2.5 rounded-full",
              isRunning && 'bg-emerald-500 animate-pulse',
              agent.status === 'completed' && 'bg-emerald-500',
              agent.status === 'failed' && 'bg-red-500',
              agent.status === 'waiting' && 'bg-amber-500',
              agent.status === 'created' && 'bg-slate-400'
            )} />
          </div>
          <span className="text-xs font-medium text-muted-foreground">
            {statusConfig.text}
          </span>
        </div>
      </div>

      {/* Metrics grid */}
      <div className="p-3 grid grid-cols-2 gap-2">
        <div className="flex items-center gap-2 p-2 rounded-md bg-muted/30 border border-border">
          <Repeat className="w-3.5 h-3.5 text-muted-foreground" />
          <div>
            <div className="text-xs text-muted-foreground">迭代次数</div>
            <div className="text-sm text-foreground font-medium">{agent.iterations || 0}</div>
          </div>
        </div>

        <div className="flex items-center gap-2 p-2 rounded-md bg-muted/30 border border-border">
          <Zap className="w-3.5 h-3.5 text-muted-foreground" />
          <div>
            <div className="text-xs text-muted-foreground">工具调用</div>
            <div className="text-sm text-foreground font-medium">{agent.tool_calls || 0}</div>
          </div>
        </div>

        {!agent.parent_agent_id && (
          <div className="flex items-center gap-2 p-2 rounded-md bg-muted/30 border border-border">
            <Bug className={cn("w-3.5 h-3.5", agent.findings_count > 0 ? 'text-red-500' : 'text-muted-foreground')} />
            <div>
              <div className="text-xs text-muted-foreground">发现漏洞</div>
              <div className={cn("text-sm font-medium", agent.findings_count > 0 ? 'text-red-600' : 'text-foreground')}>
                {agent.findings_count}
              </div>
            </div>
          </div>
        )}

        {agent.parent_agent_id && (
          <div className="flex items-center gap-2 p-2 rounded-md bg-muted/30 border border-border">
            <Clock className="w-3.5 h-3.5 text-muted-foreground" />
            <div>
              <div className="text-xs text-muted-foreground">
                {agent.duration_ms ? "耗时" : "状态"}
              </div>
              <div className="text-sm text-foreground font-medium">
                {agent.duration_ms
                  ? `${(agent.duration_ms / 1000).toFixed(1)}s`
                  : (AGENT_STATUS_CONFIG[agent.status]?.text || agent.status)
                }
              </div>
            </div>
          </div>
        )}

        <div className="flex items-center gap-2 p-2 rounded-md bg-muted/30 border border-border">
          <FileCode className="w-3.5 h-3.5 text-muted-foreground" />
          <div>
            <div className="text-xs text-muted-foreground">Token用量</div>
            <div className="text-sm text-foreground font-medium">
              {((agent.tokens_used || 0) / 1000).toFixed(1)}k
            </div>
          </div>
        </div>
      </div>

      {/* Task description */}
      {agent.task_description && (
        <div className="px-3 pb-3">
          <div className="p-2.5 rounded-md bg-muted/30 border border-border">
            <div className="flex items-center gap-1.5 mb-1.5">
              <Clock className="w-3 h-3 text-muted-foreground" />
              <span className="text-xs text-muted-foreground font-medium">当前任务</span>
            </div>
            <p className="text-xs text-foreground leading-relaxed line-clamp-3">
              {agent.task_description}
            </p>
          </div>
        </div>
      )}

      {agent.children && agent.children.length > 0 && (
        <div className="px-3 pb-3">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Network className="w-3 h-3" />
            <span>{agent.children.length} 个子Agent</span>
          </div>
        </div>
      )}
    </div>
  );
});

export default AgentDetailPanel;
