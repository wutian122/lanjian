import { useState } from "react";
import { ChevronDown, ChevronRight, CheckCircle2, XCircle, Terminal, ShieldX, FlaskConical, Bug, CloudOff } from "lucide-react";
import type { SandboxAttempt } from "../types";

interface FindingSandboxEvidenceProps {
  attempts: SandboxAttempt[] | null | undefined;
}

export function FindingSandboxEvidence({ attempts }: FindingSandboxEvidenceProps) {
  if (!attempts || attempts.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
        <XCircle className="h-4 w-4 flex-shrink-0" />
        <span>{"\u672a\u8fdb\u884c\u6c99\u7bb1\u9a8c\u8bc1"}</span>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {attempts.map((attempt, i) => (
        <SandboxAttemptCard key={i} attempt={attempt} index={i} />
      ))}
    </div>
  );
}

function SandboxAttemptCard({ attempt, index }: { attempt: SandboxAttempt; index: number }) {
  const [showCommand, setShowCommand] = useState(false);
  const [showOutput, setShowOutput] = useState(false);

  return (
    <div className="rounded-md border border-border bg-card p-3">
      <div className="flex items-center gap-2 flex-wrap">
        {attempt.success ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700 dark:bg-green-900/30 dark:text-green-400">
            <CheckCircle2 className="h-3 w-3" />
            {"\u9a8c\u8bc1\u6210\u529f"}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700 dark:bg-red-900/30 dark:text-red-400">
            <XCircle className="h-3 w-3" />
            {"\u9a8c\u8bc1\u5931\u8d25"}
          </span>
        )}
        <span className="text-xs text-muted-foreground">
          {"\u9000\u51fa\u7801"}: {attempt.exit_code ?? "N/A"}
        </span>
        {attempt.finding_id && (
          <span className="inline-flex items-center rounded-full border border-border px-1.5 py-0.5 text-xs text-muted-foreground">
            ID: {attempt.finding_id}
          </span>
        )}
        {attempt.weak_evidence && (
          <span className="inline-flex items-center rounded-full bg-yellow-100 px-1.5 py-0.5 text-xs text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400">
            {"\u5f31\u8bc1\u636e"}
          </span>
        )}
        {/* Task 17\uff1a\u6c99\u7bb1\u8bc1\u636e\u56db\u8bed\u4e49\u6807\u8bb0\u2014\u2014\u533a\u5206\u771f\u5b9e\u52a8\u6001\u786e\u8ba4/\u6f14\u793a\u6027\u9759\u6001\u786e\u8ba4/\u9a8c\u8bc1\u5668\u6545\u969c/\u73af\u5883\u6545\u969c */}
        {attempt.infra_error && (
          <span
            className="inline-flex items-center gap-1 rounded-full bg-gray-200 px-2 py-0.5 text-xs font-medium text-gray-700 dark:bg-gray-700/50 dark:text-gray-300"
            title="\u6c99\u7bb1\u955c\u50cf\u7f3a\u5931\u6216 Docker \u73af\u5883\u6545\u969c\uff0c\u547d\u4ee4\u672a\u5728\u5bb9\u5668\u5185\u771f\u5b9e\u6267\u884c\uff08\u4e0d\u4ee3\u8868\u6f0f\u6d1e\u4e0d\u53ef\u590d\u73b0\uff09"
          >
            <CloudOff className="h-3 w-3" />
            {"\u6c99\u7bb1\u73af\u5883\u6545\u969c"}
          </span>
        )}
        {attempt.fabricated && (
          <span
            className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700 dark:bg-red-900/30 dark:text-red-400"
            title="LLM \u58f0\u79f0\u7684\u786e\u8ba4\u8f93\u51fa\u542b\u6a21\u62df/\u6e90\u7801\u7f3a\u5931\u7b7e\u540d\uff0c\u8bc1\u636e\u4e0d\u53ef\u4fe1\uff0c\u5df2\u6392\u9664\u51fa\u9a8c\u8bc1\u5224\u5b9a"
          >
            <ShieldX className="h-3 w-3" />
            {"\u4f2a\u9020\u8bc1\u636e\u5df2\u6392\u9664"}
          </span>
        )}
        {attempt.static_evidence && (
          <span
            className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"
            title="\u786e\u5b9a\u6027 PoC \u6a21\u677f\u7684\u6f14\u793a\u6027\u9759\u6001\u786e\u8ba4\uff0c\u975e\u771f\u5b9e\u52a8\u6001\u5229\u7528\u6210\u529f"
          >
            <FlaskConical className="h-3 w-3" />
            {"\u6f14\u793a\u6027\u9759\u6001\u786e\u8ba4"}
          </span>
        )}
        {attempt.poc_error && (
          <span
            className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
            title="\u9a8c\u8bc1\u5668\uff08PoC \u811a\u672c\uff09\u81ea\u8eab\u5d29\u6e83\uff0c\u4e0e\u6f0f\u6d1e\u672a\u590d\u73b0\u8bed\u4e49\u5206\u6863"
          >
            <Bug className="h-3 w-3" />
            {"\u9a8c\u8bc1\u5668\u5d29\u6e83"}
            {attempt.poc_error_type && (
              <span className="font-mono text-[10px] opacity-80">
                ({attempt.poc_error_type})
              </span>
            )}
          </span>
        )}
      </div>

      <div className="mt-2 space-y-1">
        <button
          type="button"
          onClick={() => setShowCommand(!showCommand)}
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          {showCommand ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          <Terminal className="h-3 w-3" />
          {"\u67e5\u770b\u547d\u4ee4"}
        </button>
        {showCommand && (
          <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted p-2 text-xs leading-relaxed">
            {attempt.command}
          </pre>
        )}

        <button
          type="button"
          onClick={() => setShowOutput(!showOutput)}
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          {showOutput ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          <Terminal className="h-3 w-3" />
          {"\u67e5\u770b\u8f93\u51fa"}
        </button>
        {showOutput && (
          <pre className="mt-1 max-h-96 overflow-auto rounded bg-muted p-2 text-xs leading-relaxed">
            {attempt.evidence_summary}
          </pre>
        )}
      </div>
    </div>
  );
}
