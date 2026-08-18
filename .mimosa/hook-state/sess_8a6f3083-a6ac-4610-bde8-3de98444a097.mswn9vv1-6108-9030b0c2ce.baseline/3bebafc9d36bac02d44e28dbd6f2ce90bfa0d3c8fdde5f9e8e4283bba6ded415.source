/**
 * Progress Dialog
 * Enterprise Blue-White UI
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { Dialog, DialogOverlay, DialogPortal } from "@/components/ui/dialog";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X as XIcon, Activity, Cpu, HardDrive, AlertTriangle, CheckCircle2 } from "lucide-react";
import { cn, calculateTaskProgress } from "@/shared/utils/utils";
import * as VisuallyHidden from "@radix-ui/react-visually-hidden";
import { taskControl } from "@/shared/services/taskControl";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";

interface TerminalProgressDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    taskId: string | null;
    taskType: "repository" | "zip";
}

interface LogEntry {
    id: string;
    timestamp: string;
    message: string;
    type: "info" | "success" | "error" | "warning";
}

export default function TerminalProgressDialog({
    open,
    onOpenChange,
    taskId,
    taskType
}: TerminalProgressDialogProps) {
    const [logs, setLogs] = useState<LogEntry[]>([]);
    const [isCompleted, setIsCompleted] = useState(false);
    const [isFailed, setIsFailed] = useState(false);
    const [isCancelled, setIsCancelled] = useState(false);
    const [currentTime, setCurrentTime] = useState(new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    const logsEndRef = useRef<HTMLDivElement>(null);
    const pollIntervalRef = useRef<number | null>(null);
    const hasInitializedLogsRef = useRef(false);

    // Refs for state accessed in intervals/effects to avoid dependency cycles
    const logsRef = useRef<LogEntry[]>([]);
    const isCompletedRef = useRef(false);
    const isFailedRef = useRef(false);
    const isCancelledRef = useRef(false);

    // Sync refs with state
    useEffect(() => {
        logsRef.current = logs;
    }, [logs]);

    useEffect(() => {
        isCompletedRef.current = isCompleted;
    }, [isCompleted]);

    useEffect(() => {
        isFailedRef.current = isFailed;
    }, [isFailed]);

    useEffect(() => {
        isCancelledRef.current = isCancelled;
    }, [isCancelled]);

    // 添加日志条目
    const addLog = useCallback((message: string, type: LogEntry["type"] = "info") => {
        const timestamp = new Date().toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit"
        });
        const newLog = { id: Math.random().toString(36).substr(2, 9), timestamp, message, type };
        setLogs(prev => [...prev, newLog]);
    }, []);

    // 取消任务处理
    const handleCancel = async () => {
        if (!taskId) return;

        if (!confirm('确定要取消此任务吗？已分析的结果将被保留。')) {
            return;
        }

        // 1. 标记任务为取消状态
        taskControl.cancelTask(taskId);
        setIsCancelled(true);
        addLog("[ERR] 用户取消任务，正在停止...", "error");

        // 2. 立即更新数据库状态
        try {
            const { api } = await import("@/shared/config/database");
            // biome-ignore lint/suspicious/noExplicitAny: API type mismatch workaround
            await api.updateAuditTask(taskId, { status: 'cancelled' } as any);
            addLog("[WARN] 任务状态已更新为已取消", "warning");
            toast.success("任务已取消");
        } catch (error) {
            console.error('更新取消状态失败:', error);
            toast.warning("任务已标记取消，后台正在停止...");
        }
    };

    // 自动滚动到底部
    // biome-ignore lint/correctness/useExhaustiveDependencies: We want to scroll when logs change
    useEffect(() => {
        logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [logs]);

    // 实时更新光标处的时间
    useEffect(() => {
        if (!open || isCompleted || isFailed || isCancelled) {
            return;
        }

        const timeInterval = setInterval(() => {
            setCurrentTime(new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
        }, 1000);

        return () => {
            clearInterval(timeInterval);
        };
    }, [open, isCompleted, isFailed, isCancelled]);

    // 轮询任务状态
    useEffect(() => {
        if (!open || !taskId) {
            // 清理状态
            setLogs([]);
            logsRef.current = [];
            setIsCompleted(false);
            setIsFailed(false);
            setIsCancelled(false);
            hasInitializedLogsRef.current = false;
            if (pollIntervalRef.current) {
                clearInterval(pollIntervalRef.current);
                pollIntervalRef.current = null;
            }
            return;
        }

        // 只初始化日志一次（防止React严格模式重复）
        if (!hasInitializedLogsRef.current) {
            hasInitializedLogsRef.current = true;

            // 初始化日志
            addLog("[INFO] 审计任务已启动", "info");
            addLog(`TASK_ID: ${taskId}`, "info");
            addLog(`TYPE: ${taskType === "repository" ? "REPO_AUDIT" : "ZIP_AUDIT"}`, "info");
            addLog("[WAIT] 正在初始化审计环境...", "info");
        }

        let lastScannedFiles = 0;
        let lastIssuesCount = 0;
        let lastTotalLines = 0;
        let lastStatus = "";
        let _pollCount = 0;
        let hasDataChange = false;
        let isFirstPoll = true;

        // 开始轮询
        const pollTask = async () => {
            // 如果任务已完成或失败，停止轮询
            if (isCompletedRef.current || isFailedRef.current) {
                if (pollIntervalRef.current) {
                    clearInterval(pollIntervalRef.current);
                    pollIntervalRef.current = null;
                }
                return;
            }

            try {
                _pollCount++;
                hasDataChange = false;

                const requestStartTime = Date.now();

                // 使用 api.getAuditTaskById 获取任务状态
                const { api } = await import("@/shared/config/database");
                const task = await api.getAuditTaskById(taskId);

                const requestDuration = Date.now() - requestStartTime;

                if (!task) {
                    addLog(`[ERR] 任务不存在 (${requestDuration}ms)`, "error");
                    throw new Error("任务不存在");
                }

                // 检查是否有数据变化
                const statusChanged = task.status !== lastStatus;
                const filesChanged = task.scanned_files !== lastScannedFiles;
                const issuesChanged = task.issues_count !== lastIssuesCount;
                const linesChanged = task.total_lines !== lastTotalLines;

                hasDataChange = statusChanged || filesChanged || issuesChanged || linesChanged;

                // 标记首次轮询已完成
                if (isFirstPoll) {
                    isFirstPoll = false;
                }

                // 只在有变化时显示请求/响应信息（跳过 pending 状态）
                if (hasDataChange && task.status !== "pending") {
                    addLog(`[NET] 正在获取任务状态...`, "info");
                    addLog(
                        `[OK] 状态: ${task.status} | 文件: ${task.scanned_files}/${task.total_files} | 问题: ${task.issues_count} (${requestDuration}ms)`,
                        "success"
                    );
                }

                // 更新上次状态
                if (statusChanged) {
                    lastStatus = task.status;
                }

                // 检查任务状态
                if (task.status === "pending") {
                    // 静默跳过 pending 状态，不显示任何日志
                } else if (task.status === "running") {
                    // 首次进入运行状态
                    if (statusChanged && logsRef.current.filter(l => l.message.includes("开始扫描")).length === 0) {
                        addLog("[SCAN] 开始扫描代码文件...", "info");
                        if (task.project) {
                            addLog(`[PROJ] 项目: ${task.project.name}`, "info");
                            if (task.branch_name) {
                                addLog(`[BRCH] 分支: ${task.branch_name}`, "info");
                            }
                        }
                    }

                    // 显示进度更新（仅在有变化时）
                    if (filesChanged && task.scanned_files > lastScannedFiles) {
                        const progress = calculateTaskProgress(task.scanned_files, task.total_files);
                        const filesProcessed = task.scanned_files - lastScannedFiles;
                        addLog(
                            `[PROG] 扫描进度: ${task.scanned_files || 0}/${task.total_files || 0} 文件 (${progress}%) [+${filesProcessed}]`,
                            "info"
                        );
                        lastScannedFiles = task.scanned_files;
                    }

                    // 显示问题发现（仅在有变化时）
                    if (issuesChanged && task.issues_count > lastIssuesCount) {
                        const newIssues = task.issues_count - lastIssuesCount;
                        addLog(`[WARN] 发现 ${newIssues} 个新问题 (总计: ${task.issues_count})`, "warning");
                        lastIssuesCount = task.issues_count;
                    }

                    // 显示代码行数（仅在有变化时）
                    if (linesChanged && task.total_lines > lastTotalLines) {
                        const newLines = task.total_lines - lastTotalLines;
                        addLog(`[STAT] 已分析 ${task.total_lines.toLocaleString()} 行代码 [+${newLines.toLocaleString()}]`, "info");
                        lastTotalLines = task.total_lines;
                    }
                } else if (task.status === "completed") {
                    // 任务完成
                    if (!isCompletedRef.current) {
                        addLog("", "info"); // 空行分隔
                        addLog("[DONE] 代码扫描完成", "success");
                        addLog("----------------------------------", "info");
                        addLog(`[STAT] 总计扫描: ${task.total_files} 个文件`, "success");
                        addLog(`[STAT] 总计分析: ${task.total_lines.toLocaleString()} 行代码`, "success");
                        addLog(`[RSLT] 发现问题: ${task.issues_count} 个`, task.issues_count > 0 ? "warning" : "success");

                        // 解析问题类型分布
                        if (task.issues_count > 0) {
                            try {
                                const { api: apiImport } = await import("@/shared/config/database");
                                const issues = await apiImport.getAuditIssues(taskId);

                                const severityCounts = {
                                    critical: issues.filter(i => i.severity === 'critical').length,
                                    high: issues.filter(i => i.severity === 'high').length,
                                    medium: issues.filter(i => i.severity === 'medium').length,
                                    low: issues.filter(i => i.severity === 'low').length
                                };

                                if (severityCounts.critical > 0) {
                                    addLog(`  [CRIT] 严重: ${severityCounts.critical} 个`, "error");
                                }
                                if (severityCounts.high > 0) {
                                    addLog(`  [HIGH] 高: ${severityCounts.high} 个`, "warning");
                                }
                                if (severityCounts.medium > 0) {
                                    addLog(`  [MED] 中等: ${severityCounts.medium} 个`, "warning");
                                }
                                if (severityCounts.low > 0) {
                                    addLog(`  [LOW] 低: ${severityCounts.low} 个`, "info");
                                }
                            } catch (_e) {
                                // 静默处理错误
                            }
                        }

                        addLog(`[SCOR] 质量评分: ${task.quality_score.toFixed(1)}/100`, "success");
                        addLog("----------------------------------", "info");
                        addLog("[FIN] 审计任务已完成！", "success");

                        if (task.completed_at) {
                            const startTime = new Date(task.created_at).getTime();
                            const endTime = new Date(task.completed_at).getTime();
                            const duration = Math.round((endTime - startTime) / 1000);
                            addLog(`[TIME] 总耗时: ${duration} 秒`, "info");
                        }

                        setIsCompleted(true);
                        if (pollIntervalRef.current) {
                            clearInterval(pollIntervalRef.current);
                            pollIntervalRef.current = null;
                        }
                    }
                } else if (task.status === "cancelled") {
                    // 任务被取消
                    if (!isCancelledRef.current) {
                        addLog("", "info"); // 空行分隔
                        addLog("[STOP] 任务已被用户取消", "warning");
                        addLog("----------------------------------", "warning");
                        addLog(`[STAT] 完成统计:`, "info");
                        addLog(`  • 已分析文件: ${task.scanned_files}/${task.total_files}`, "info");
                        addLog(`  • 发现问题: ${task.issues_count} 个`, "info");
                        addLog(`  • 代码行数: ${task.total_lines.toLocaleString()} 行`, "info");
                        addLog("----------------------------------", "warning");
                        addLog("[SAVE] 已分析的结果已保存到数据库", "success");

                        setIsCancelled(true);
                        if (pollIntervalRef.current) {
                            clearInterval(pollIntervalRef.current);
                            pollIntervalRef.current = null;
                        }
                    }
                } else if (task.status === "failed") {
                    // 任务失败
                    if (!isFailedRef.current) {
                        addLog("", "info"); // 空行分隔
                        addLog("[FAIL] 审计任务执行失败", "error");
                        addLog("----------------------------------", "error");

                        // 尝试从日志系统获取具体错误信息
                        try {
                            const { logger } = await import("@/shared/utils/logger");
                            const recentLogs = logger.getLogs({
                                startTime: Date.now() - 60000, // 最近1分钟
                            });

                            // 查找与当前任务相关的错误
                            const taskErrors = recentLogs
                                .filter(log =>
                                    log.level === 'ERROR' &&
                                    (log.message.includes(taskId) ||
                                        log.message.includes('审计') ||
                                        log.message.includes('API'))
                                )
                                .slice(-3); // 最近3条错误

                            if (taskErrors.length > 0) {
                                addLog("具体错误信息:", "error");
                                taskErrors.forEach(log => {
                                    addLog(`  • ${log.message}`, "error");
                                    if (log.data?.error) {
                                        const errorMsg = typeof log.data.error === 'string'
                                            ? log.data.error
                                            : log.data.error.message || JSON.stringify(log.data.error);
                                        addLog(`    ${errorMsg}`, "error");
                                    }
                                });
                            } else {
                                // 如果没有找到具体错误，显示常见原因
                                addLog("可能的原因:", "error");
                                addLog("  • 网络连接问题", "error");
                                addLog("  • 仓库访问权限不足（私有仓库需配置 Token）", "error");
                                addLog("  • GitHub/GitLab API 限流", "error");
                                addLog("  • LLM API 配置错误或额度不足", "error");
                            }
                        } catch (_e) {
                            // 如果获取日志失败，显示常见原因
                            addLog("可能的原因:", "error");
                            addLog("  • 网络连接问题", "error");
                            addLog("  • 仓库访问权限不足（私有仓库需配置 Token）", "error");
                            addLog("  • GitHub/GitLab API 限流", "error");
                            addLog("  • LLM API 配置错误或额度不足", "error");
                        }

                        addLog("----------------------------------", "error");
                        addLog("[HINT] 建议: 检查系统配置和网络连接后重试", "warning");
                        addLog("[LOGS] 查看完整日志: 导航栏 -> 系统日志", "warning");

                        setIsFailed(true);
                        if (pollIntervalRef.current) {
                            clearInterval(pollIntervalRef.current);
                            pollIntervalRef.current = null;
                        }
                    }
                }
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : "未知错误";
                addLog(`[ERR] ${errorMessage}`, "error");
                // 不中断轮询，继续尝试
            }
        };

        // 立即执行一次
        pollTask();

        // 设置定时轮询（每2秒）
        pollIntervalRef.current = window.setInterval(pollTask, 2000);

        // 清理函数
        return () => {
            if (pollIntervalRef.current) {
                clearInterval(pollIntervalRef.current);
                pollIntervalRef.current = null;
            }
        };
    }, [open, taskId, taskType, addLog]);

    // 获取日志颜色
    const getLogColor = (type: LogEntry["type"]) => {
        switch (type) {
            case "success":
                return "text-emerald-600 dark:text-emerald-400";
            case "error":
                return "text-rose-600 dark:text-rose-400";
            case "warning":
                return "text-amber-600 dark:text-amber-400";
            default:
                return "text-muted-foreground";
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogPortal>
                <DialogOverlay className="bg-black/50 dark:bg-black/85 backdrop-blur-md" />
                <DialogPrimitive.Content
                    className={cn(
                        "fixed left-[50%] top-[50%] z-50 translate-x-[-50%] translate-y-[-50%]",
                        "w-[95vw] max-w-[1000px] h-[85vh] max-h-[700px]",
                        "bg-white dark:bg-[#08090d] border border-slate-200 dark:border-[#1a2535] rounded overflow-hidden",
                        "shadow-xl dark:shadow-[0_0_60px_rgba(0,0,0,0.8),inset_0_1px_0_rgba(255,255,255,0.02)]",
                        "data-[state=open]:animate-in data-[state=closed]:animate-out",
                        "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
                        "data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
                        "duration-300 font-mono"
                    )}
                    onPointerDownOutside={(e) => e.preventDefault()}
                    onInteractOutside={(e) => e.preventDefault()}
                >
                    <VisuallyHidden.Root>
                        <DialogPrimitive.Title>审计进度监控</DialogPrimitive.Title>
                        <DialogPrimitive.Description>
                            实时显示代码审计任务的执行进度和详细信息
                        </DialogPrimitive.Description>
                    </VisuallyHidden.Root>

                    {/* Header */}
                    <div className="flex items-center justify-between px-4 py-3 bg-card border-b border-border">
                        <div className="flex items-center gap-3">
                            <Activity className="w-5 h-5 text-primary" />
                            <div>
                                <span className="text-lg font-semibold text-foreground">审计进度</span>
                                <span className="text-xs text-muted-foreground ml-2">v3.0</span>
                            </div>
                        </div>

                        <div className="flex items-center gap-4">
                            <div className="flex items-center gap-2.5 px-3 py-1.5 bg-muted rounded-lg border border-border">
                                <div className={`w-2.5 h-2.5 rounded-full transition-all duration-300 ${!isCompleted && !isFailed && !isCancelled
                                    ? 'bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.5)] animate-pulse'
                                    : 'bg-slate-300'}`} />
                                <div className={`w-2.5 h-2.5 rounded-full transition-all duration-300 ${isFailed
                                    ? 'bg-red-500 shadow-[0_0_10px_rgba(239,68,68,0.5)]'
                                    : 'bg-slate-300'}`} />
                                <div className={`w-2.5 h-2.5 rounded-full transition-all duration-300 ${isCompleted
                                    ? 'bg-cyan-500 shadow-[0_0_10px_rgba(6,182,212,0.5)]'
                                    : 'bg-slate-300'}`} />
                            </div>

                            <button
                                type="button"
                                className="w-8 h-8 flex items-center justify-center hover:bg-red-50 rounded-lg transition-all duration-200 group"
                                onClick={() => onOpenChange(false)}
                            >
                                <XIcon className="w-5 h-5 text-muted-foreground group-hover:text-red-500 transition-colors" />
                            </button>
                        </div>
                    </div>

                    {/* Main Content */}
                    <div className="flex h-[calc(100%-56px)]">
                        {/* Left Sidebar - Task Info */}
                        <div className="w-48 p-4 border-r border-border bg-muted/30 flex flex-col gap-4">
                            <div className="space-y-1.5">
                                <div className="text-xs font-medium text-muted-foreground">任务 ID</div>
                                <div className="text-xs text-foreground truncate bg-muted p-2.5 rounded-lg border border-border">
                                    {taskId?.slice(0, 8)}...
                                </div>
                            </div>

                            <div className="space-y-1.5">
                                <div className="text-xs font-medium text-muted-foreground">类型</div>
                                <div className="flex items-center gap-2 bg-muted p-2.5 rounded-lg border border-border">
                                    {taskType === 'repository'
                                        ? <Cpu className="w-3.5 h-3.5 text-cyan-600" />
                                        : <HardDrive className="w-3.5 h-3.5 text-amber-600" />}
                                    <span className="text-xs font-semibold text-foreground">{taskType === 'repository' ? '仓库审计' : 'ZIP审计'}</span>
                                </div>
                            </div>

                            <div className="flex-1" />

                            {/* Status Badge */}
                            <div className="space-y-2">
                                <div className="text-xs font-medium text-muted-foreground">状态</div>
                                {isCancelled ? (
                                    <Badge variant="outline" className="w-full justify-center border-amber-200 bg-amber-50 text-amber-700">已取消</Badge>
                                ) : isCompleted ? (
                                    <Badge variant="outline" className="w-full justify-center border-emerald-200 bg-emerald-50 text-emerald-700">已完成</Badge>
                                ) : isFailed ? (
                                    <Badge variant="outline" className="w-full justify-center border-red-200 bg-red-50 text-red-700">失败</Badge>
                                ) : (
                                    <Badge variant="outline" className="w-full justify-center border-blue-200 bg-blue-50 text-blue-700 animate-pulse">运行中</Badge>
                                )}
                            </div>
                        </div>

                        {/* Terminal Screen */}
                        <div className="flex-1 flex flex-col">
                            {/* Log Output */}
                            <div className="flex-1 bg-muted/20 p-4 overflow-y-auto text-sm custom-scrollbar relative">
                                <div className="relative z-10 space-y-0.5 pb-10">
                                    {logs.map((log) => (
                                        <div key={log.id} className="flex items-start gap-3 hover:bg-muted/50 px-2 py-0.5 transition-colors group rounded">
                                            <span className="text-muted-foreground text-xs flex-shrink-0 w-20">
                                                {log.timestamp}
                                            </span>
                                            <span className={`${getLogColor(log.type)} flex-1 text-sm`}>
                                                {log.message}
                                            </span>
                                        </div>
                                    ))}

                                    {!isCompleted && !isFailed && !isCancelled && (
                                        <div className="flex items-center gap-3 mt-4 px-2">
                                            <span className="text-muted-foreground text-xs w-20">{currentTime}</span>
                                            <span className="text-primary animate-pulse font-bold">_</span>
                                        </div>
                                    )}
                                    <div ref={logsEndRef} />
                                </div>
                            </div>

                            {/* Bottom Controls */}
                            <div className="h-14 px-4 border-t border-border bg-muted/30 flex items-center justify-between">
                                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                    <Activity className="w-3.5 h-3.5" />
                                    <span>
                                        {isCompleted ? "任务已完成" : isFailed ? "任务失败" : isCancelled ? "任务已取消" : "执行中..."}
                                    </span>
                                </div>

                                <div className="flex items-center gap-3">
                                    {!isCompleted && !isFailed && !isCancelled && (
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            onClick={handleCancel}
                                            className="h-8 border-amber-200 text-amber-700 hover:bg-amber-50 hover:border-amber-300 text-xs"
                                        >
                                            <AlertTriangle className="w-3 h-3 mr-1.5" />
                                            取消任务
                                        </Button>
                                    )}

                                    {isFailed && (
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            onClick={() => window.open('/logs', '_blank')}
                                            className="h-8 text-xs"
                                        >
                                            <Activity className="w-3 h-3 mr-1.5" />
                                            查看日志
                                        </Button>
                                    )}

                                    {(isCompleted || isFailed || isCancelled) && (
                                        <Button
                                            size="sm"
                                            onClick={() => onOpenChange(false)}
                                            className="h-8 text-xs"
                                        >
                                            <CheckCircle2 className="w-3 h-3 mr-1.5" />
                                            确认
                                        </Button>
                                    )}
                                </div>
                            </div>
                        </div>
                    </div>
                </DialogPrimitive.Content>
            </DialogPortal>
        </Dialog>
    );
}
