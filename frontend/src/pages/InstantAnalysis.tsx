/**
 * Instant Analysis Page
 * Enterprise Blue-White UI
 */

import { useState, useRef, useEffect } from "react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionPanel } from "@/components/ui/section-panel";
import { StatusBadge } from "@/components/ui/status-badge";
import { getSeverityText } from "@/shared/utils/uiText";
import { cn } from "@/shared/utils/utils";
import {
  AlertTriangle,
  CheckCircle,
  Clock,
  Code,
  FileText,
  Info,
  Lightbulb,
  Shield,
  Target,
  TrendingUp,
  Upload,
  Zap,
  X,
  Download,
  History,
  ChevronRight,
  MessageSquare,
} from "lucide-react";
import { CodeAnalysisEngine } from "@/features/analysis/services";
import { api } from "@/shared/config/database";
import type { CodeAnalysisResult, InstantAnalysis as InstantAnalysisType } from "@/shared/types";
import { toast } from "sonner";
import InstantExportDialog from "@/components/reports/InstantExportDialog";
import { getPromptTemplates, type PromptTemplate } from "@/shared/api/prompts";

// AI explanation parser
function parseAIExplanation(aiExplanation: string) {
  try {
    const parsed = JSON.parse(aiExplanation);
    if (parsed.xai) return parsed.xai;
    if (parsed.what || parsed.why || parsed.how) return parsed;
    return null;
  } catch (error) {
    return null;
  }
}

export default function InstantAnalysis() {
  const [code, setCode] = useState("");
  const [language, setLanguage] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<CodeAnalysisResult | null>(null);
  const [analysisTime, setAnalysisTime] = useState(0);
  const [exportDialogOpen, setExportDialogOpen] = useState(false);
  const [currentAnalysisId, setCurrentAnalysisId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const loadingCardRef = useRef<HTMLDivElement>(null);

  // History related state
  const [showHistory, setShowHistory] = useState(false);
  const [historyRecords, setHistoryRecords] = useState<InstantAnalysisType[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null);

  // Prompt templates
  const [promptTemplates, setPromptTemplates] = useState<PromptTemplate[]>([]);
  const [selectedPromptTemplateId, setSelectedPromptTemplateId] = useState<string>("");

  const supportedLanguages = CodeAnalysisEngine.getSupportedLanguages();

  // 语言自动检测
  function detectLanguage(code: string): string {
    const patterns: [RegExp, string][] = [
      [/<\?php/i, 'php'],
      [/import\s+React|from\s+['"]react['"]|jsx|tsx/i, 'javascript'],
      [/^import\s+\w+|def\s+\w+\s*\(|if\s+__name__\s*==\s*['"]__main__['"]/im, 'python'],
      [/public\s+(class|static|void)|System\.out\.print|@Test|@Override/, 'java'],
      [/package\s+\w+|func\s+\w+|import\s+\(/, 'go'],
      [/fn\s+\w+|impl\s+\w+|let\s+mut|use\s+\w+::/, 'rust'],
      [/namespace\s+\w+|using\s+System|class\s+\w+\s*:\s*public/, 'cpp'],
      [/require|gem\s+|module\s+\w+|def\s+\w+/, 'ruby'],
      [/import\s+Swift|func\s+\w+|var\s+\w+\s*:\s*/, 'swift'],
      [/fun\s+\w+|val\s+\w+|var\s+\w+/, 'kotlin'],
      [/\$\(|ready\(|function\s+\w+\s*\(|console\./, 'javascript'],
    ];
    const trimmed = code.trim();
    for (const [pattern, lang] of patterns) {
      if (pattern.test(trimmed)) return lang;
    }
    // 通过分号密度判断
    const semicolons = (trimmed.match(/;/g) || []).length;
    const lines = trimmed.split('\n').length;
    if (semicolons > lines * 0.3) return 'javascript';
    return '';
  }

  // Load prompt templates
  useEffect(() => {
    const loadPromptTemplates = async () => {
      try {
        const res = await getPromptTemplates({ is_active: true });
        setPromptTemplates(res.items);
        const defaultTemplate = res.items.find(t => t.is_default);
        if (defaultTemplate) {
          setSelectedPromptTemplateId(defaultTemplate.id);
        } else if (res.items.length > 0) {
          setSelectedPromptTemplateId(res.items[0].id);
        }
      } catch (error) {
        console.error("加载提示词模板失败:", error);
      }
    };
    loadPromptTemplates();
  }, []);

  // 代码变化时自动检测语言
  useEffect(() => {
    if (code.trim() && !language) {
      const detected = detectLanguage(code);
      if (detected) {
        setLanguage(detected);
      }
    }
  }, [code]);

  // Load history
  const loadHistory = async () => {
    setLoadingHistory(true);
    try {
      const records = await api.getInstantAnalyses();
      setHistoryRecords(records);
    } catch (error) {
      console.error('Failed to load history:', error);
      toast.error('加载历史记录失败');
    } finally {
      setLoadingHistory(false);
    }
  };

  // View history record details
  const viewHistoryRecord = (record: InstantAnalysisType) => {
    try {
      const analysisResult = JSON.parse(record.analysis_result) as CodeAnalysisResult;
      setResult(analysisResult);
      setLanguage(record.language);
      setAnalysisTime(record.analysis_time);
      setSelectedHistoryId(record.id);
      setCurrentAnalysisId(record.id);
      setShowHistory(false);
      toast.success('已加载历史分析结果');
    } catch (error) {
      console.error('Failed to parse history record:', error);
      toast.error('解析历史记录失败');
    }
  };

  // Format date
  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  // Delete single history record
  const deleteHistoryRecord = async (e: React.MouseEvent, recordId: string) => {
    e.stopPropagation();
    try {
      await api.deleteInstantAnalysis(recordId);
      setHistoryRecords(prev => prev.filter(r => r.id !== recordId));
      if (selectedHistoryId === recordId) {
        setSelectedHistoryId(null);
        setResult(null);
      }
      toast.success('删除成功');
    } catch (error) {
      console.error('Failed to delete history:', error);
      toast.error('删除失败');
    }
  };

  // Clear all history
  const clearAllHistory = async () => {
    if (!confirm('确定要清空所有历史记录吗？此操作不可恢复。')) return;
    try {
      await api.deleteAllInstantAnalyses();
      setHistoryRecords([]);
      setSelectedHistoryId(null);
      toast.success('已清空所有历史记录');
    } catch (error) {
      console.error('Failed to clear history:', error);
      toast.error('清空失败');
    }
  };

  // Toggle history panel
  const toggleHistory = () => {
    if (!showHistory) {
      loadHistory();
    }
    setShowHistory(!showHistory);
  };

  // Auto scroll to loading card when analyzing
  useEffect(() => {
    if (analyzing && loadingCardRef.current) {
      requestAnimationFrame(() => {
        setTimeout(() => {
          if (loadingCardRef.current) {
            loadingCardRef.current.scrollIntoView({
              behavior: 'smooth',
              block: 'center'
            });
          }
        }, 50);
      });
    }
  }, [analyzing]);

  // Example codes
  const exampleCodes = {
    javascript: `// 示例JavaScript代码 - 包含多种问题
var userName = "admin";
var password = "123456"; // 硬编码密码

function validateUser(input) {
    if (input == userName) { // 使用 == 比较
        console.log("User validated"); // 生产代码中的console.log
        return true;
    }
    return false;
}

// 性能问题：循环中重复计算长度
function processItems(items) {
    for (var i = 0; i < items.length; i++) {
        for (var j = 0; j < items.length; j++) {
            console.log(items[i] + items[j]);
        }
    }
}

// 安全问题：使用eval
function executeCode(userInput) {
    eval(userInput); // 危险的eval使用
}`,
    python: `# 示例Python代码 - 包含多种问题
import *  # 通配符导入

password = "secret123"  # 硬编码密码

def process_data(data):
    try:
        result = []
        for item in data:
            print(item)  # 使用print而非logging
            result.append(item * 2)
        return result
    except:  # 裸露的except语句
        pass`,
    java: `// 示例Java代码 - 包含多种问题
public class Example {
    private String password = "admin123"; // 硬编码密码

    public void processData() {
        System.out.println("Processing..."); // 使用System.out.print

        try {
            String data = getData();
        } catch (Exception e) {
            // 空的异常处理
        }
    }
}`
  };

  const handleAnalyze = async () => {
    if (!code.trim()) {
      toast.error("请输入要分析的代码");
      return;
    }
    if (!language) {
      toast.error("请选择编程语言");
      return;
    }

    try {
      setAnalyzing(true);
      setTimeout(() => {
        window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
      }, 100);

      const startTime = Date.now();
      const analysisResult = await CodeAnalysisEngine.analyzeCode(code, language, selectedPromptTemplateId || undefined);
      const endTime = Date.now();
      const duration = (endTime - startTime) / 1000;

      setResult(analysisResult);
      setAnalysisTime(analysisResult.analysis_time || duration);
      setCurrentAnalysisId(analysisResult.analysis_id || null);

      toast.success(`分析完成！发现 ${analysisResult.issues.length} 个问题`);
    } catch (error: any) {
      console.error('Analysis failed:', error);
      toast.error(error?.message || "分析失败，请稍后重试");
    } finally {
      setAnalyzing(false);
      setCode("");
    }
  };

  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      const content = e.target?.result as string;
      setCode(content);

      const extension = file.name.split('.').pop()?.toLowerCase();
      const languageMap: Record<string, string> = {
        'js': 'javascript', 'jsx': 'javascript', 'ts': 'typescript', 'tsx': 'typescript',
        'py': 'python', 'java': 'java', 'go': 'go', 'rs': 'rust',
        'cpp': 'cpp', 'c': 'cpp', 'cs': 'csharp', 'php': 'php',
        'rb': 'ruby', 'swift': 'swift', 'kt': 'kotlin'
      };

      if (extension && languageMap[extension]) {
        setLanguage(languageMap[extension]);
      }
    };
    reader.readAsText(file);
  };

  const loadExampleCode = (lang: string) => {
    const example = exampleCodes[lang as keyof typeof exampleCodes];
    if (example) {
      setCode(example);
      setLanguage(lang);
      toast.success(`已加载${lang}示例代码`);
    }
  };

  const getTypeIcon = (type: string) => {
    switch (type) {
      case 'security': return <Shield className="w-4 h-4" />;
      case 'bug': return <AlertTriangle className="w-4 h-4" />;
      case 'performance': return <Zap className="w-4 h-4" />;
      case 'style': return <Code className="w-4 h-4" />;
      case 'maintainability': return <FileText className="w-4 h-4" />;
      default: return <Info className="w-4 h-4" />;
    }
  };

  const clearAnalysis = () => {
    setCode("");
    setLanguage("");
    setResult(null);
    setAnalysisTime(0);
  };

  // Render issue with enterprise styling
  const renderIssue = (issue: any, index: number) => (
    <div key={index} className="rounded-lg border border-border bg-card p-4 mb-3 hover:shadow-sm transition-all">
      <div className="flex items-start justify-between mb-3 pb-3 border-b border-border">
        <div className="flex items-start space-x-3">
          <div className={cn(
            "w-10 h-10 rounded-lg flex items-center justify-center",
            issue.severity === 'critical' ? 'bg-red-50 text-red-600' :
            issue.severity === 'high' ? 'bg-orange-50 text-orange-600' :
            issue.severity === 'medium' ? 'bg-amber-50 text-amber-600' :
            'bg-blue-50 text-blue-600'
          )}>
            {getTypeIcon(issue.type)}
          </div>
          <div className="flex-1">
            <h4 className="font-semibold text-sm text-foreground mb-1">{issue.title}</h4>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>第 {issue.line} 行</span>
              {issue.column && <span>，第 {issue.column} 列</span>}
            </div>
          </div>
        </div>
        <StatusBadge
          type="severity"
          value={issue.severity}
          label={getSeverityText(issue.severity)}
        />
      </div>

      {issue.description && (
        <div className="bg-muted/50 border border-border rounded-md p-3 mb-3">
          <div className="flex items-center gap-1.5 mb-1.5">
            <Info className="w-3.5 h-3.5 text-muted-foreground" />
            <span className="font-medium text-muted-foreground text-xs">问题详情</span>
          </div>
          <p className="text-foreground text-sm leading-relaxed">{issue.description}</p>
        </div>
      )}

      {issue.code_snippet && (
        <div className="rounded-md border border-border mb-3 overflow-hidden">
          <div className="flex items-center justify-between px-3 py-1.5 bg-muted/50 border-b border-border">
            <div className="flex items-center gap-1.5">
              <Code className="w-3.5 h-3.5 text-muted-foreground" />
              <span className="text-xs text-muted-foreground font-medium">代码片段</span>
            </div>
            <span className="text-xs text-muted-foreground">第 {issue.line} 行</span>
          </div>
          <pre className="p-3 text-xs font-mono text-foreground overflow-x-auto bg-slate-50 dark:bg-slate-950">
            <code>{issue.code_snippet}</code>
          </pre>
        </div>
      )}

      <div className="space-y-2">
        {issue.suggestion && (
          <div className="bg-blue-50 dark:bg-blue-950/20 border border-blue-200 dark:border-blue-800 rounded-md p-3">
            <div className="flex items-center gap-2 mb-1.5">
              <Lightbulb className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />
              <span className="font-medium text-blue-700 dark:text-blue-300 text-xs">修复建议</span>
            </div>
            <p className="text-blue-800 dark:text-blue-200/80 text-sm leading-relaxed">{issue.suggestion}</p>
          </div>
        )}

        {issue.ai_explanation && (() => {
          const parsedExplanation = parseAIExplanation(issue.ai_explanation);

          if (parsedExplanation) {
            return (
              <div className="bg-violet-50 dark:bg-violet-950/20 border border-violet-200 dark:border-violet-800 rounded-md p-3">
                <div className="flex items-center gap-2 mb-2">
                  <Zap className="w-3.5 h-3.5 text-violet-600 dark:text-violet-400" />
                  <span className="font-medium text-violet-700 dark:text-violet-300 text-xs">AI 解释</span>
                </div>
                <div className="space-y-2 text-sm">
                  {parsedExplanation.what && (
                    <div className="border-l-2 border-red-400 pl-2">
                      <span className="font-medium text-red-600 dark:text-red-400">问题：</span>
                      <span className="text-foreground">{parsedExplanation.what}</span>
                    </div>
                  )}
                  {parsedExplanation.why && (
                    <div className="border-l-2 border-amber-400 pl-2">
                      <span className="font-medium text-amber-600 dark:text-amber-400">原因：</span>
                      <span className="text-foreground">{parsedExplanation.why}</span>
                    </div>
                  )}
                  {parsedExplanation.how && (
                    <div className="border-l-2 border-emerald-400 pl-2">
                      <span className="font-medium text-emerald-600 dark:text-emerald-400">方案：</span>
                      <span className="text-foreground">{parsedExplanation.how}</span>
                    </div>
                  )}
                  {parsedExplanation.learn_more && (
                    <div className="border-l-2 border-blue-400 pl-2">
                      <span className="font-medium text-blue-600 dark:text-blue-400">链接：</span>
                      <a
                        href={parsedExplanation.learn_more}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-600 dark:text-blue-400 hover:underline"
                      >
                        {parsedExplanation.learn_more}
                      </a>
                    </div>
                  )}
                </div>
              </div>
            );
          } else {
            return (
              <div className="bg-violet-50 dark:bg-violet-950/20 border border-violet-200 dark:border-violet-800 rounded-md p-3">
                <div className="flex items-center gap-2 mb-1.5">
                  <Zap className="w-3.5 h-3.5 text-violet-600 dark:text-violet-400" />
                  <span className="font-medium text-violet-700 dark:text-violet-300 text-xs">AI 解释</span>
                </div>
                <p className="text-foreground text-sm leading-relaxed">{issue.ai_explanation}</p>
              </div>
            );
          }
        })()}
      </div>
    </div>
  );

  return (
    <div className="space-y-6 p-6 min-h-screen">
      {/* Page Header */}
      <PageHeader
        eyebrow="即时分析"
        title="即时代码分析"
        description="提交代码片段或文件，快速获取安全风险和修复建议。"
        actions={
          <div className="relative group">
            <Button
              onClick={handleAnalyze}
              disabled={!code.trim() || !language || analyzing}
              size="lg"
            >
              {analyzing ? (
                <>
                  <div className="loading-spinner w-4 h-4 mr-2"></div>
                  分析中...
                </>
              ) : (
                <>
                  <Zap className="w-4 h-4 mr-2" />
                  开始分析
                </>
              )}
            </Button>
            {(!code.trim() || !language) && !analyzing && (
              <div className="absolute top-full mt-1 right-0 bg-popover text-popover-foreground text-xs px-3 py-1.5 rounded-md shadow-md border border-border whitespace-nowrap z-50 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
                {!code.trim() ? '请先输入或粘贴要分析的代码' : '请先选择编程语言'}
              </div>
            )}
          </div>
        }
      />

      {/* History Panel */}
      {showHistory && (
        <SectionPanel
          title="分析历史记录"
          actions={
            <div className="flex items-center gap-2">
              {historyRecords.length > 0 && (
                <Button variant="outline" onClick={clearAllHistory} size="sm">
                  清空全部
                </Button>
              )}
              <Button variant="ghost" onClick={() => setShowHistory(false)} size="sm" className="h-8 w-8 p-0">
                <X className="w-4 h-4" />
              </Button>
            </div>
          }
        >
          {loadingHistory ? (
            <div className="text-center py-8">
              <div className="loading-spinner mx-auto mb-4"></div>
              <p className="text-muted-foreground">加载中...</p>
            </div>
          ) : historyRecords.length === 0 ? (
            <div className="text-center py-8">
              <History className="w-12 h-12 text-muted-foreground mx-auto mb-3" />
              <p className="text-base font-medium text-muted-foreground">暂无历史记录</p>
              <p className="text-sm text-muted-foreground">完成代码分析后，记录将显示在这里</p>
            </div>
          ) : (
            <ScrollArea className="h-[400px]">
              <div className="space-y-2">
                {historyRecords.map((record) => (
                  <div
                    key={record.id}
                    className={cn(
                      "p-3 rounded-lg border transition-colors cursor-pointer",
                      selectedHistoryId === record.id
                        ? 'bg-primary/5 border-primary/30'
                        : 'bg-muted/30 border-border hover:bg-muted'
                    )}
                    onClick={() => viewHistoryRecord(record)}
                  >
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <Badge variant="secondary">{record.language}</Badge>
                        <span className="text-sm text-muted-foreground">{formatDate(record.created_at)}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <StatusBadge
                          value={record.quality_score >= 80 ? 'completed' : record.quality_score >= 60 ? 'running' : 'failed'}
                          label={`评分: ${(record.quality_score ?? 0).toFixed(1)}`}
                        />
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => deleteHistoryRecord(e, record.id)}
                          className="h-6 w-6 p-0"
                        >
                          <X className="w-3 h-3" />
                        </Button>
                        <ChevronRight className="w-4 h-4 text-muted-foreground" />
                      </div>
                    </div>
                    <div className="flex items-center gap-4 text-xs text-muted-foreground">
                      <span className="flex items-center gap-1">
                        <AlertTriangle className="w-3 h-3" />
                        {record.issues_count} 个问题
                      </span>
                      <span className="flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {(record.analysis_time ?? 0).toFixed(2)}s
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </ScrollArea>
          )}
        </SectionPanel>
      )}

      {/* Code Input Area */}
      <SectionPanel
        title="代码输入"
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={toggleHistory}
              size="sm"
            >
              <History className="w-4 h-4 mr-2" />
              历史记录
            </Button>
            {result && (
              <Button variant="outline" onClick={clearAnalysis} size="sm">
                <X className="w-4 h-4 mr-2" />
                重新分析
              </Button>
            )}
          </div>
        }
      >
        <div className="space-y-4">
          {/* Toolbar */}
          <div className="flex flex-col sm:flex-row gap-3">
            <div className="flex-1 space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">编程语言</label>
              <Select value={language} onValueChange={setLanguage}>
                <SelectTrigger className="h-10">
                  <SelectValue placeholder="选择编程语言" />
                </SelectTrigger>
                <SelectContent>
                  {supportedLanguages.map((lang) => (
                    <SelectItem key={lang} value={lang}>
                      {lang.charAt(0).toUpperCase() + lang.slice(1)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex-1 space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">提示词模板</label>
              <Select value={selectedPromptTemplateId} onValueChange={setSelectedPromptTemplateId}>
                <SelectTrigger className="h-10">
                  <div className="flex items-center gap-2">
                    <MessageSquare className="w-4 h-4 text-muted-foreground" />
                    <SelectValue placeholder="选择提示词模板" />
                  </div>
                </SelectTrigger>
                <SelectContent>
                  {promptTemplates.map((pt) => (
                    <SelectItem key={pt.id} value={pt.id}>
                      {pt.name} {pt.is_default && '(默认)'}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-end">
              <Button
                variant="outline"
                onClick={() => fileInputRef.current?.click()}
                disabled={analyzing}
                className="h-10"
              >
                <Upload className="w-4 h-4 mr-2" />
                上传文件
              </Button>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept=".js,.jsx,.ts,.tsx,.py,.java,.go,.rs,.cpp,.c,.cc,.h,.hh,.cs,.php,.rb,.swift,.kt"
              onChange={handleFileUpload}
              className="hidden"
            />
          </div>

          {/* Quick Examples */}
          <div className="flex flex-wrap gap-2 items-center p-2 bg-muted/50 rounded-md">
            <span className="text-xs font-medium text-muted-foreground mr-1">示例：</span>
            {['javascript', 'python', 'java'].map((lang) => (
              <Button
                key={lang}
                variant="outline"
                size="sm"
                onClick={() => loadExampleCode(lang)}
                disabled={analyzing}
                className="h-7 px-2.5 text-xs"
              >
                {lang.charAt(0).toUpperCase() + lang.slice(1)}
              </Button>
            ))}
          </div>

          {/* Code Editor */}
          <div>
            <Textarea
              placeholder="// 粘贴代码或上传文件..."
              value={code}
              onChange={(e) => setCode(e.target.value)}
              className="min-h-[300px] font-mono text-sm bg-slate-50 dark:bg-slate-950 border-border p-4 focus-visible:ring-1"
              disabled={analyzing}
            />
            <div className="text-xs text-muted-foreground mt-1 text-right">
              {code.length} 字符，{code.split('\n').length} 行
            </div>
          </div>

          {/* Analyze Button */}
          <div className="relative group">
            <Button
              onClick={handleAnalyze}
              disabled={!code.trim() || !language || analyzing}
              className="w-full h-11 text-base font-semibold"
            >
              {analyzing ? (
                <>
                  <div className="loading-spinner w-4 h-4 mr-2"></div>
                  分析中...
                </>
              ) : (
                <>
                  <Zap className="w-4 h-4 mr-2" />
                  开始分析
                </>
              )}
            </Button>
            {(!code.trim() || !language) && !analyzing && (
              <div className="absolute top-full mt-1 left-1/2 -translate-x-1/2 bg-popover text-popover-foreground text-xs px-3 py-1.5 rounded-md shadow-md border border-border whitespace-nowrap z-50 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
                {!code.trim() ? '请先输入或粘贴要分析的代码' : '请先选择编程语言'}
              </div>
            )}
          </div>
        </div>
      </SectionPanel>

      {/* Analysis Results */}
      {result && (
        <div className="space-y-6">
          {/* Results Overview */}
          <SectionPanel
            title="分析结果"
            actions={
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="gap-1">
                  <Clock className="w-3 h-3" />
                  {(analysisTime ?? 0).toFixed(2)}s
                </Badge>
                <Badge variant="secondary">{language}</Badge>
                <Button size="sm" onClick={() => setExportDialogOpen(true)}>
                  <Download className="w-4 h-4 mr-2" />
                  导出报告
                </Button>
              </div>
            }
          >
            {/* Core Metrics */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
              <div className="rounded-lg border border-border bg-card p-4 text-center">
                <div className="mb-2">
                  <Target className="w-6 h-6 text-blue-500 mx-auto" />
                </div>
                <div className="text-2xl font-semibold text-foreground mb-1">
                  {(result.quality_score ?? 0).toFixed(1)}
                </div>
                <p className="text-sm text-muted-foreground mb-2">质量评分</p>
                <Progress value={result.quality_score ?? 0} className="h-2" />
              </div>

              <div className="rounded-lg border border-border bg-card p-4 text-center">
                <div className="mb-2">
                  <AlertTriangle className="w-6 h-6 text-red-500 mx-auto" />
                </div>
                <div className="text-2xl font-semibold text-red-600 mb-1">
                  {(result.summary?.critical_issues ?? 0) + (result.summary?.high_issues ?? 0)}
                </div>
                <p className="text-sm text-muted-foreground mb-1">严重问题</p>
                <p className="text-xs text-red-500">需要立即处理</p>
              </div>

              <div className="rounded-lg border border-border bg-card p-4 text-center">
                <div className="mb-2">
                  <Info className="w-6 h-6 text-amber-500 mx-auto" />
                </div>
                <div className="text-2xl font-semibold text-amber-600 mb-1">
                  {(result.summary?.medium_issues ?? 0) + (result.summary?.low_issues ?? 0)}
                </div>
                <p className="text-sm text-muted-foreground mb-1">一般问题</p>
                <p className="text-xs text-amber-500">建议优化</p>
              </div>

              <div className="rounded-lg border border-border bg-card p-4 text-center">
                <div className="mb-2">
                  <FileText className="w-6 h-6 text-emerald-500 mx-auto" />
                </div>
                <div className="text-2xl font-semibold text-emerald-600 mb-1">
                  {result.issues.length}
                </div>
                <p className="text-sm text-muted-foreground mb-1">总问题数</p>
                <p className="text-xs text-emerald-500">已全部识别</p>
              </div>
            </div>

            {/* Detailed Metrics */}
            <div className="bg-muted/50 rounded-lg p-4">
              <h3 className="text-sm font-medium mb-4 flex items-center gap-2">
                <TrendingUp className="w-4 h-4" />
                详细指标
              </h3>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-6">
                {[
                  { label: '复杂度', value: result.metrics?.complexity ?? 0 },
                  { label: '可维护性', value: result.metrics?.maintainability ?? 0 },
                  { label: '安全性', value: result.metrics?.security ?? 0 },
                  { label: '性能', value: result.metrics?.performance ?? 0 },
                ].map((metric) => (
                  <div key={metric.label} className="text-center">
                    <div className="text-xl font-semibold text-foreground mb-1">{metric.value}</div>
                    <p className="text-xs text-muted-foreground mb-2">{metric.label}</p>
                    <Progress value={metric.value} className="h-2" />
                  </div>
                ))}
              </div>
            </div>
          </SectionPanel>

          {/* Issues Detail */}
          <SectionPanel
            title={`发现的问题 (${result.issues.length})`}
          >
            {result.issues.length > 0 ? (
              <Tabs defaultValue="all" className="w-full">
                <TabsList className="grid w-full grid-cols-4 h-auto rounded-lg mb-6">
                  <TabsTrigger value="all" className="text-xs py-2">
                    全部 ({result.issues.length})
                  </TabsTrigger>
                  <TabsTrigger value="critical" className="text-xs py-2">
                    严重 ({result.issues.filter(i => i.severity === 'critical').length})
                  </TabsTrigger>
                  <TabsTrigger value="high" className="text-xs py-2">
                    高危 ({result.issues.filter(i => i.severity === 'high').length})
                  </TabsTrigger>
                  <TabsTrigger value="medium" className="text-xs py-2">
                    中危 ({result.issues.filter(i => i.severity === 'medium').length})
                  </TabsTrigger>
                </TabsList>

                <TabsContent value="all" className="space-y-4 mt-0">
                  {result.issues.map((issue, index) => renderIssue(issue, index))}
                </TabsContent>

                {['critical', 'high', 'medium'].map(severity => (
                  <TabsContent key={severity} value={severity} className="space-y-4 mt-0">
                    {result.issues.filter(issue => issue.severity === severity).length > 0 ? (
                      result.issues.filter(issue => issue.severity === severity).map((issue, index) => renderIssue(issue, index))
                    ) : (
                      <div className="text-center py-12 border border-dashed rounded-lg">
                        <CheckCircle className="w-12 h-12 text-emerald-500 mx-auto mb-3" />
                        <h3 className="text-base font-medium text-foreground mb-1">
                          没有发现{severity === 'critical' ? '严重' : severity === 'high' ? '高优先级' : '中等优先级'}问题
                        </h3>
                        <p className="text-sm text-muted-foreground">代码在此级别的检查中表现良好</p>
                      </div>
                    )}
                  </TabsContent>
                ))}
              </Tabs>
            ) : (
              <div className="text-center py-16 border border-dashed rounded-lg">
                <CheckCircle className="w-16 h-16 text-emerald-500 mx-auto mb-4" />
                <h3 className="text-xl font-semibold text-emerald-700 dark:text-emerald-400 mb-2">代码质量优秀！</h3>
                <p className="text-emerald-600 dark:text-emerald-400/80 mb-4">恭喜！没有发现任何问题</p>
                <div className="bg-emerald-50 dark:bg-emerald-950/20 border border-emerald-200 dark:border-emerald-800 p-4 max-w-md mx-auto rounded-lg">
                  <p className="text-emerald-700 dark:text-emerald-300/80 text-sm">
                    您的代码通过了所有质量检查，包括安全性、性能、可维护性等各个方面的评估。
                  </p>
                </div>
              </div>
            )}
          </SectionPanel>
        </div>
      )}

      {/* Analyzing State */}
      {analyzing && (
        <SectionPanel>
          <div ref={loadingCardRef} className="py-16 text-center">
            <div className="w-16 h-16 bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 rounded-xl flex items-center justify-center mx-auto mb-4">
              <div className="loading-spinner w-8 h-8"></div>
            </div>
            <h3 className="text-xl font-semibold text-foreground mb-2">AI正在分析您的代码</h3>
            <p className="text-muted-foreground mb-4">请稍候，这通常需要至少30秒钟...</p>
            <p className="text-muted-foreground text-sm mb-4">分析时长取决于您的网络环境、代码长度以及使用的模型等因素</p>
            <div className="bg-blue-50 dark:bg-blue-950/20 border border-blue-200 dark:border-blue-800 p-4 max-w-md mx-auto rounded-lg">
              <p className="text-blue-700 dark:text-blue-300 text-sm">
                正在进行安全检测、性能分析、代码风格检查等多维度评估<br />
                请勿离开页面！
              </p>
            </div>
          </div>
        </SectionPanel>
      )}

      {/* Export Report Dialog */}
      {result && (
        <InstantExportDialog
          open={exportDialogOpen}
          onOpenChange={setExportDialogOpen}
          analysisId={currentAnalysisId}
          analysisResult={result}
          language={language}
          analysisTime={analysisTime}
        />
      )}
    </div>
  );
}
