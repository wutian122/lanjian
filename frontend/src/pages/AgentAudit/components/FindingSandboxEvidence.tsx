import { useState } from "react";
import { ChevronDown, ChevronRight, CheckCircle2, XCircle, Terminal } from "lucide-react";
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
