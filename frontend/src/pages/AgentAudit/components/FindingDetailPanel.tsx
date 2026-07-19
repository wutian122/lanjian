import { X } from "lucide-react";
import { FindingSandboxEvidence } from "./FindingSandboxEvidence";
import type { AgentFinding } from "@/shared/api/agentTasks";

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
            {finding.file_path}:{finding.line_start}
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
