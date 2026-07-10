import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { PageHeader } from "@/components/layout/PageHeader";
import { getAgentTasks, type AgentTask } from "@/shared/api/agentTasks";
import { api } from "@/shared/config/database";
import type { AuditTask } from "@/shared/types";
import { TaskReferencePanel, type FilterState } from "./components/TaskReferencePanel";
import { ChatWorkspace } from "./components/ChatWorkspace";

export default function AIPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [agentTasks, setAgentTasks] = useState<AgentTask[]>([]);
  const [regularTasks, setRegularTasks] = useState<AuditTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterState, setFilterState] = useState<FilterState>({ search: "", status: "all", type: "all" });
  const referencedTaskId = searchParams.get("taskId");
  const referencedTaskKind = (searchParams.get("kind") as "agent" | "regular" | null) || null;

  useEffect(() => {
    const load = async () => {
      try {
        setLoading(true);
        const [agentResult, regularResult] = await Promise.all([
          getAgentTasks(),
          api.getAuditTasks(),
        ]);
        setAgentTasks(agentResult);
        setRegularTasks(regularResult);
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, []);

  const activeTask = useMemo(() => {
    if (!referencedTaskId || !referencedTaskKind) return null;
    if (referencedTaskKind === "regular") {
      return regularTasks.find(task => task.id === referencedTaskId) ?? null;
    }
    return agentTasks.find(task => task.id === referencedTaskId) ?? null;
  }, [referencedTaskId, referencedTaskKind, agentTasks, regularTasks]);

  const handleReferenceAgentTask = (task: AgentTask) => {
    setSearchParams({ taskId: task.id, kind: "agent" });
  };

  const handleReferenceRegularTask = (task: AuditTask) => {
    setSearchParams({ taskId: task.id, kind: "regular" });
  };

  const handleOpenWorkbench = (task: AgentTask) => {
    navigate(`/agent-audit/${task.id}`);
  };

  return (
    <div className="flex flex-col gap-6 h-[calc(100vh-80px)]">
      <PageHeader
        title="AI 审计控制中心"
        description="会话管理、任务引用与全局 AI 协同入口"
      />

      <div className="flex-1 grid grid-cols-[1fr_360px] grid-rows-[1fr] overflow-hidden rounded-xl border border-border bg-card shadow-card">
        <div className="h-full min-w-0 min-h-0">
          {loading ? (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">正在加载 AI 控制中心...</div>
          ) : (
            <ChatWorkspace key={referencedTaskId || "no-task"} activeTask={activeTask} taskKind={referencedTaskKind} />
          )}
        </div>

        <TaskReferencePanel
          agentTasks={agentTasks}
          regularTasks={regularTasks}
          activeTaskId={referencedTaskId}
          activeTaskKind={referencedTaskKind}
          onReferenceAgent={handleReferenceAgentTask}
          onReferenceRegular={handleReferenceRegularTask}
          onOpenAgentWorkbench={handleOpenWorkbench}
          onOpenRegularTask={(task) => navigate(`/tasks/${task.id}`)}
          filterState={filterState}
          onFilterChange={setFilterState}
        />
      </div>
    </div>
  );
}
