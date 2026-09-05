import { X, AlertTriangle, CloudOff } from "lucide-react";
import { FindingSandboxEvidence } from "./FindingSandboxEvidence";
import type { AgentFinding } from "@/shared/api/agentTasks";

/**
 * sandbox_skip_reason 中文释义（Task 8 M2 承接）。
 * 数据链路：后端 finding.sandbox_skip_reason → 落库 verification_result JSON；
 * 程序化取值见 orchestrator.py（放行路径）与 verification.py（弹性退出/无模板
 * 豁免），LLM 亦可在 Final Answer 自由标注（未知值回退显示原始字符串）。
 */
const SKIP_REASON_LABELS: Record<string, string> = {
  gate_release_after_max_redispatch: "门禁放行：达到最大重派次数后放行，未经沙箱复现",
  orchestrator_max_iterations_exhausted: "编排器迭代次数耗尽，未经沙箱验证",
  elastic_exit: "弹性退出：验证重试预算耗尽后豁免沙箱验证",
  no_poc_template: "无 PoC 模板：该漏洞类型无确定性沙箱验证模板",
};

function skipReasonLabel(reason: string): string {
  return SKIP_REASON_LABELS[reason] ?? reason;
}

interface FindingDetailPanelProps {
  finding: AgentFinding;
  onClose: () => void;
}

export function FindingDetailPanel({ finding, onClose }: FindingDetailPanelProps) {
  const verdictLabel = (() => {
    const vs = finding.verification_status;
    if (vs === "confirmed") return { text: "已验证", color: "text-green-600" };
    if (vs === "static_confirmed") return { text: "静态确认", color: "text-blue-600" };
    if (vs === "not_reproducible") return { text: "不可复现", color: "text-amber-600" };
    if (vs === "false_positive") return { text: "误报", color: "text-gray-500" };
    return { text: "待确认", color: "text-yellow-600" };
  })();

  const verResult = finding.verification_result;

  // Task 17：finding 级沙箱环境故障提示。后端 compute_verification_status 的 infra
  // 分支（全部 attempt 为 infra_error：Docker 缺席/镜像缺失/连接失败）落库形态为
  // 终态 needs_context + verification_note 含 "infra_error=True"（notes 键值拼接）。
  // 与 sandbox_skip_reason（豁免/放行）语义独立：环境故障不是"不可复现"，也不是豁免。
  const infraFailure =
    finding.verification_status === "needs_context" &&
    /infra_error=True/.test(verResult?.verification_note ?? "");

  return (
    <div className="fixed inset-y-0 right-0 z-50 w-[480px] max-w-full bg-white dark:bg-gray-900 shadow-xl border-l dark:border-gray-700 overflow-y-auto">
      <div className="flex items-center justify-between px-4 py-3 border-b dark:border-gray-700 sticky top-0 bg-white dark:bg-gray-900 z-10">
        <h3 className="font-semibold text-sm truncate">{finding.title}</h3>
        <button onClick={onClose} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded">
          <X className="w-4 h-4" />
        </button>
      </div>
      <div className="p-4 space-y-4">
        {/* 基本信息 */}
        <div>
          <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">漏洞类型</div>
          <div className="text-sm font-medium">{finding.vulnerability_type}</div>
        </div>
        <div>
          <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">严重程度</div>
          <div className="text-sm font-medium">{finding.severity}</div>
        </div>
        <div>
          <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">文件位置</div>
          <div className="text-sm font-mono text-blue-700 dark:text-blue-400 break-all">
            {finding.file_path || "-"}
            {finding.line_start != null && `:${finding.line_start}`}
          </div>
        </div>

        {/* 验证状态 */}
        <div>
          <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">验证状态</div>
          <div className={`text-sm font-semibold ${verdictLabel.color}`}>
            {verdictLabel.text}
            {finding.verification_method && (
              <span className="text-xs text-gray-400 dark:text-gray-500 ml-2">({finding.verification_method})</span>
            )}
          </div>
        </div>

        {/* 沙箱跳过原因（未沙箱验证的豁免/放行说明，Task 8 M2 承接） */}
        {verResult?.sandbox_skip_reason && (
          <div>
            <div className="text-xs text-amber-600 dark:text-amber-400 mb-1 flex items-center gap-1">
              <AlertTriangle className="w-3 h-3" />
              未沙箱验证
            </div>
            <div className="text-sm bg-amber-50 dark:bg-amber-900/30 rounded p-3 text-amber-700 dark:text-amber-300">
              {skipReasonLabel(verResult.sandbox_skip_reason)}
              <span className="block text-xs text-amber-500 dark:text-amber-500/70 mt-1 font-mono">
                {verResult.sandbox_skip_reason}
              </span>
            </div>
          </div>
        )}

        {/* 沙箱环境故障（Task 1/17：全部 attempt 因 Docker/镜像故障未进容器，
            终态 needs_context——不代表漏洞不可复现） */}
        {infraFailure && (
          <div>
            <div className="text-xs text-red-500 dark:text-red-400 mb-1 flex items-center gap-1">
              <CloudOff className="w-3 h-3" />
              沙箱环境故障
            </div>
            <div className="text-sm bg-red-50 dark:bg-red-900/30 rounded p-3 text-red-700 dark:text-red-300">
              沙箱环境故障（未能验证）：沙箱镜像缺失或 Docker 环境不可用，本次未能执行动态验证，不代表漏洞不可复现。
              {verResult?.verification_note && (
                <span className="block text-xs text-red-400 dark:text-red-500/70 mt-1 font-mono whitespace-pre-wrap">
                  {verResult.verification_note}
                </span>
              )}
            </div>
          </div>
        )}

        {/* 验证结果详情 */}
        {verResult?.details && (
          <div>
            <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">验证详情</div>
            <div className="text-sm bg-gray-50 dark:bg-gray-800 rounded p-3 whitespace-pre-wrap">{verResult.details}</div>
          </div>
        )}

        {/* 失败原因 */}
        {verResult?.failure_reason && (
          <div>
            <div className="text-xs text-red-500 dark:text-red-400 mb-1">失败原因</div>
            <div className="text-sm bg-red-50 dark:bg-red-900/30 rounded p-3 text-red-700 dark:text-red-300">{verResult.failure_reason}</div>
          </div>
        )}

        {/* 沙箱尝试列表 */}
        {finding.sandbox_attempts && finding.sandbox_attempts.length > 0 && (
          <div>
            <div className="text-xs text-gray-500 dark:text-gray-400 mb-2">
              沙箱验证尝试 ({finding.sandbox_attempts.length})
            </div>
            <FindingSandboxEvidence attempts={finding.sandbox_attempts} />
          </div>
        )}

        {/* 代码片段 */}
        {finding.code_snippet && (
          <div>
            <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">代码片段</div>
            <pre className="text-xs bg-gray-50 dark:bg-gray-800 rounded p-3 overflow-x-auto">{finding.code_snippet}</pre>
          </div>
        )}

        {/* 修复建议 */}
        {finding.suggestion && (
          <div>
            <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">修复建议</div>
            <div className="text-sm bg-green-50 dark:bg-green-900/30 rounded p-3">{finding.suggestion}</div>
          </div>
        )}
      </div>
    </div>
  );
}
