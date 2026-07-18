/**
 * System Config Component
 * Enterprise Blue-White UI
 */

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import {
  Settings, Save, RotateCcw, Eye, EyeOff, CheckCircle2, AlertCircle, Shield,
  Info, Zap, Globe, PlayCircle, Brain, Key, Copy, Trash2, Terminal, ServerCrash, Wifi, WifiOff
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/shared/api/database";
import { apiClient } from "@/shared/api/serverClient";
import EmbeddingConfig from "@/components/agent/EmbeddingConfig";
import { generateSSHKey, getSSHKey, deleteSSHKey, testSSHKey, clearKnownHosts } from "@/shared/api/sshKeys";
import { SectionPanel } from "@/components/ui/section-panel";
import { Switch } from "@/components/ui/switch";

// LLM Providers — 顺序不可变
const LLM_PROVIDERS = [
  { value: 'openai',          label: 'OpenAI' },
  { value: 'openai-response', label: 'OpenAI-Response' },
  { value: 'gemini',          label: 'Gemini' },
  { value: 'anthropic',       label: 'Anthropic' },
  { value: 'azure-openai',    label: 'Azure OpenAI' },
  { value: 'new-api',         label: 'New API' },
  { value: 'ollama',          label: 'Ollama' },
];

const DEFAULT_MODELS: Record<string, string> = {
  openai: 'gpt-4o',
  'openai-response': 'gpt-4o',
  gemini: 'gemini-2.0-flash',
  anthropic: 'claude-sonnet-4-20250514',
  'azure-openai': 'gpt-4o',
  'new-api': 'gpt-4o',
  ollama: 'llama3',
};

interface SystemConfigData {
  llmProvider: string; llmApiKey: string; llmModel: string; llmBaseUrl: string;
  llmTimeout: number; llmTemperature: number; llmMaxTokens: number;
  // Agent超时配置
  llmFirstTokenTimeout: number; llmStreamTimeout: number;
  agentTimeout: number; subAgentTimeout: number; toolTimeout: number;
  githubToken: string; gitlabToken: string; giteaToken: string;
  maxAnalyzeFiles: number; llmConcurrency: number; llmGapMs: number; llmRatePerMinute: number; outputLanguage: string;
  sandboxNetworkEnabled: boolean;
}

export function SystemConfig() {
  const [config, setConfig] = useState<SystemConfigData | null>(null);
  const [loading, setLoading] = useState(true);
  const [showApiKey, setShowApiKey] = useState(false);
  const [hasChanges, setHasChanges] = useState(false);
  const [testingLLM, setTestingLLM] = useState(false);
  const [llmTestResult, setLlmTestResult] = useState<{ success: boolean; message: string; debug?: Record<string, unknown> } | null>(null);
  const [showDebugInfo, setShowDebugInfo] = useState(true);

  // SSH Key states
  const [sshKey, setSSHKey] = useState<{ has_key: boolean; public_key?: string; fingerprint?: string }>({ has_key: false });
  const [generatingKey, setGeneratingKey] = useState(false);
  const [deletingKey, setDeletingKey] = useState(false);
  const [clearingKnownHosts, setClearingKnownHosts] = useState(false);
  const [testingKey, setTestingKey] = useState(false);
  const [testRepoUrl, setTestRepoUrl] = useState("");
  const [showDeleteKeyDialog, setShowDeleteKeyDialog] = useState(false);

  // Sandbox states
  const [sandboxTestUrl, setSandboxTestUrl] = useState("https://mirrors.aliyun.com");
  const [testingNetwork, setTestingNetwork] = useState(false);
  const [sandboxTestResult, setSandboxTestResult] = useState<{success: boolean; message: string} | null>(null);
  const [sandboxInstallCmd, setSandboxInstallCmd] = useState("");
  const [executingSandbox, setExecutingSandbox] = useState(false);
  const [sandboxExecOutput, setSandboxExecOutput] = useState("");
  const [sandboxPackages, setSandboxPackages] = useState<{name: string; version: string}[]>([]);
  const [loadingPackages, setLoadingPackages] = useState(false);

  const testSandboxNetwork = async () => {
    setTestingNetwork(true);
    setSandboxTestResult(null);
    try {
      const res = await apiClient.post('/config/test-sandbox-network', { url: sandboxTestUrl });
      setSandboxTestResult(res.data);
    } catch (e: any) {
      setSandboxTestResult({ success: false, message: e?.response?.data?.detail || 'Test failed' });
    } finally {
      setTestingNetwork(false);
    }
  };

  const execSandboxCommand = async () => {
    if (!sandboxInstallCmd.trim()) return;
    setExecutingSandbox(true);
    setSandboxExecOutput('> ' + sandboxInstallCmd + '\nRunning...\n');
    try {
      const res = await apiClient.post('/config/sandbox-exec', { command: sandboxInstallCmd, timeout: 120, persistent: true });
      const data = res.data;
      let output = '> ' + sandboxInstallCmd + '\n';
      if (data.stdout) output += data.stdout;
      if (data.stderr) output += '\n[stderr]\n' + data.stderr;
      output += '\n' + (data.success ? 'Success' : 'Failed') + ' (exit: ' + data.exit_code + ')';
      setSandboxExecOutput(output);
    } catch (e: any) {
      setSandboxExecOutput('> ' + sandboxInstallCmd + '\nError: ' + (e?.response?.data?.detail || e?.message));
    } finally {
      setExecutingSandbox(false);
    }
  };

  const loadSandboxPackages = async () => {
    setLoadingPackages(true);
    try {
      const res = await apiClient.get('/config/sandbox-packages');
      setSandboxPackages(res.data.packages || []);
    } catch (e) { console.error('Failed:', e); } finally { setLoadingPackages(false); }
  };

  useEffect(() => { loadConfig(); loadSSHKey(); }, []);

  const loadConfig = async () => {
    try {
      setLoading(true);
      console.log('[SystemConfig] 开始加载配置...');

      const backendConfig = await api.getUserConfig();

      console.log('[SystemConfig] 后端返回的原始数据:', JSON.stringify(backendConfig, null, 2));

      if (backendConfig) {
        const llmConfig = backendConfig.llmConfig || {};
        const otherConfig = backendConfig.otherConfig || {};

        const newConfig = {
          llmProvider: llmConfig.llmProvider || 'openai',
          llmApiKey: llmConfig.llmApiKey || '',
          llmModel: llmConfig.llmModel || '',
          llmBaseUrl: llmConfig.llmBaseUrl || '',
          llmTimeout: llmConfig.llmTimeout || 150000,
          llmTemperature: llmConfig.llmTemperature ?? 0.1,
          llmMaxTokens: llmConfig.llmMaxTokens || 4096,
          // Agent超时配置
          llmFirstTokenTimeout: llmConfig.llmFirstTokenTimeout || 30,
          llmStreamTimeout: llmConfig.llmStreamTimeout || 60,
          agentTimeout: llmConfig.agentTimeout || 1800,
          subAgentTimeout: llmConfig.subAgentTimeout || 600,
          toolTimeout: llmConfig.toolTimeout || 60,
          githubToken: otherConfig.githubToken || '',
          gitlabToken: otherConfig.gitlabToken || '',
          giteaToken: otherConfig.giteaToken || '',
          maxAnalyzeFiles: otherConfig.maxAnalyzeFiles ?? 0,
          llmConcurrency: otherConfig.llmConcurrency || 3,
          llmGapMs: otherConfig.llmGapMs || 2000,
          llmRatePerMinute: otherConfig.llmRatePerMinute || 60,
          outputLanguage: otherConfig.outputLanguage || 'zh-CN',
          sandboxNetworkEnabled: otherConfig.sandboxNetworkEnabled ?? false,
        };

        console.log('[SystemConfig] 解析后的配置:', newConfig);
        setConfig(newConfig);

        console.log('✓ 配置已加载:', {
          provider: llmConfig.llmProvider,
          hasApiKey: !!llmConfig.llmApiKey,
          model: llmConfig.llmModel,
        });
      } else {
        console.warn('[SystemConfig] 后端返回空数据，使用默认配置');
        setConfig({
          llmProvider: 'openai', llmApiKey: '', llmModel: '', llmBaseUrl: '',
          llmTimeout: 150000, llmTemperature: 0.1, llmMaxTokens: 4096,
          llmFirstTokenTimeout: 30, llmStreamTimeout: 60,
          agentTimeout: 1800, subAgentTimeout: 600, toolTimeout: 60,
          githubToken: '', gitlabToken: '', giteaToken: '',
          maxAnalyzeFiles: 0, llmConcurrency: 3, llmGapMs: 2000, llmRatePerMinute: 60, outputLanguage: 'zh-CN',
          sandboxNetworkEnabled: false,
        });
      }
    } catch (error) {
      console.error('Failed to load config:', error);
      setConfig({
        llmProvider: 'openai', llmApiKey: '', llmModel: '', llmBaseUrl: '',
        llmTimeout: 150000, llmTemperature: 0.1, llmMaxTokens: 4096,
        llmFirstTokenTimeout: 30, llmStreamTimeout: 60,
        agentTimeout: 1800, subAgentTimeout: 600, toolTimeout: 60,
        githubToken: '', gitlabToken: '', giteaToken: '',
        maxAnalyzeFiles: 0, llmConcurrency: 3, llmGapMs: 2000, llmRatePerMinute: 60, outputLanguage: 'zh-CN',
        sandboxNetworkEnabled: false,
      });
    } finally {
      setLoading(false);
    }
  };

  // SSH Key functions
  const loadSSHKey = async () => {
    try {
      const data = await getSSHKey();
      setSSHKey(data);
    } catch (error) {
      console.error('Failed to load SSH key:', error);
    }
  };

  const handleGenerateSSHKey = async () => {
    try {
      setGeneratingKey(true);
      const data = await generateSSHKey();
      setSSHKey({ has_key: true, public_key: data.public_key, fingerprint: data.fingerprint });
      toast.success(data.message);
    } catch (error: any) {
      console.error('Failed to generate SSH key:', error);
      toast.error(error.response?.data?.detail || "生成SSH密钥失败");
    } finally {
      setGeneratingKey(false);
    }
  };

  const handleDeleteSSHKey = async () => {
    try {
      setDeletingKey(true);
      await deleteSSHKey();
      setSSHKey({ has_key: false });
      toast.success("SSH密钥已删除");
      setShowDeleteKeyDialog(false);
    } catch (error: any) {
      console.error('Failed to delete SSH key:', error);
      toast.error(error.response?.data?.detail || "删除SSH密钥失败");
    } finally {
      setDeletingKey(false);
    }
  };

  const handleTestSSHKey = async () => {
    if (!testRepoUrl) {
      toast.error("请输入仓库URL");
      return;
    }
    try {
      setTestingKey(true);
      const result = await testSSHKey(testRepoUrl);
      if (result.success) {
        toast.success("SSH连接测试成功");
        if (result.output) {
          console.log("SSH测试输出:", result.output);
        }
      } else {
        toast.error(result.message || "SSH连接测试失败", {
          description: result.output ? `详情: ${result.output.substring(0, 100)}...` : undefined,
          duration: 5000,
        });
        if (result.output) {
          console.error("SSH测试失败:", result.output);
        }
      }
    } catch (error: any) {
      console.error('Failed to test SSH key:', error);
      toast.error(error.response?.data?.detail || "测试SSH密钥失败");
    } finally {
      setTestingKey(false);
    }
  };

  const handleClearKnownHosts = async () => {
    try {
      setClearingKnownHosts(true);
      const result = await clearKnownHosts();
      if (result.success) {
        toast.success(result.message || "known_hosts已清理");
      } else {
        toast.error("清理known_hosts失败");
      }
    } catch (error: any) {
      console.error('Failed to clear known_hosts:', error);
      toast.error(error.response?.data?.detail || "清理known_hosts失败");
    } finally {
      setClearingKnownHosts(false);
    }
  };

  const handleCopyPublicKey = () => {
    if (sshKey.public_key) {
      navigator.clipboard.writeText(sshKey.public_key);
      toast.success("公钥已复制到剪贴板");
    }
  };

  const saveConfig = async () => {
    if (!config) return;

    // Base URL 必填验证
    if (!config.llmBaseUrl || !config.llmBaseUrl.match(/^https?:\/\/.+/)) {
      toast.error("API Base URL 为必填项，请输入有效的 URL 地址");
      return;
    }

    try {
      const savedConfig = await api.updateUserConfig({
        llmConfig: {
          llmProvider: config.llmProvider, llmApiKey: config.llmApiKey,
          llmModel: config.llmModel, llmBaseUrl: config.llmBaseUrl,
          llmTimeout: config.llmTimeout, llmTemperature: config.llmTemperature,
          llmMaxTokens: config.llmMaxTokens,
          // Agent超时配置
          llmFirstTokenTimeout: config.llmFirstTokenTimeout,
          llmStreamTimeout: config.llmStreamTimeout,
          agentTimeout: config.agentTimeout,
          subAgentTimeout: config.subAgentTimeout,
          toolTimeout: config.toolTimeout,
        },
        otherConfig: {
          githubToken: config.githubToken, gitlabToken: config.gitlabToken, giteaToken: config.giteaToken,
          maxAnalyzeFiles: config.maxAnalyzeFiles, llmConcurrency: config.llmConcurrency,
          llmGapMs: config.llmGapMs, llmRatePerMinute: config.llmRatePerMinute, outputLanguage: config.outputLanguage,
          sandboxNetworkEnabled: config.sandboxNetworkEnabled,
        },
      });

      if (savedConfig) {
        const llmConfig = savedConfig.llmConfig || {};
        const otherConfig = savedConfig.otherConfig || {};
        setConfig({
          llmProvider: llmConfig.llmProvider || config.llmProvider,
          llmApiKey: llmConfig.llmApiKey || '',
          llmModel: llmConfig.llmModel || '',
          llmBaseUrl: llmConfig.llmBaseUrl || '',
          llmTimeout: llmConfig.llmTimeout || 150000,
          llmTemperature: llmConfig.llmTemperature ?? 0.1,
          llmMaxTokens: llmConfig.llmMaxTokens || 4096,
          // Agent超时配置
          llmFirstTokenTimeout: llmConfig.llmFirstTokenTimeout || 30,
          llmStreamTimeout: llmConfig.llmStreamTimeout || 60,
          agentTimeout: llmConfig.agentTimeout || 1800,
          subAgentTimeout: llmConfig.subAgentTimeout || 600,
          toolTimeout: llmConfig.toolTimeout || 60,
          githubToken: otherConfig.githubToken || '',
          gitlabToken: otherConfig.gitlabToken || '',
          giteaToken: otherConfig.giteaToken || '',
          maxAnalyzeFiles: otherConfig.maxAnalyzeFiles ?? 0,
          llmConcurrency: otherConfig.llmConcurrency || 3,
          llmGapMs: otherConfig.llmGapMs || 2000,
          llmRatePerMinute: otherConfig.llmRatePerMinute || 60,
          outputLanguage: otherConfig.outputLanguage || 'zh-CN',
          sandboxNetworkEnabled: otherConfig.sandboxNetworkEnabled ?? false,
        });
      }

      setHasChanges(false);
      toast.success("配置已保存！");
    } catch (error) {
      toast.error(`保存失败: ${error instanceof Error ? error.message : '未知错误'}`);
    }
  };

  const resetConfig = async () => {
    if (!window.confirm("确定要重置为默认配置吗？")) return;
    try {
      await api.deleteUserConfig();
      await loadConfig();
      setHasChanges(false);
      toast.success("已重置为默认配置");
    } catch (error) {
      toast.error(`重置失败: ${error instanceof Error ? error.message : '未知错误'}`);
    }
  };

  const updateConfig = (key: keyof SystemConfigData, value: string | number | boolean) => {
    if (!config) return;
    setConfig(prev => prev ? { ...prev, [key]: value } : null);
    setHasChanges(true);
  };

  const testLLMConnection = async () => {
    if (!config) return;
    if (!config.llmApiKey && config.llmProvider !== 'ollama') {
      toast.error('请先配置 API Key');
      return;
    }
    setTestingLLM(true);
    setLlmTestResult(null);
    try {
      const result = await api.testLLMConnection({
        provider: config.llmProvider,
        apiKey: config.llmApiKey,
        model: config.llmModel || undefined,
        baseUrl: config.llmBaseUrl || undefined,
      });
      setLlmTestResult(result);
      if (result.success) toast.success(`连接成功！模型: ${result.model}`);
      else toast.error(`连接失败: ${result.message}`);
    } catch (error) {
      const msg = error instanceof Error ? error.message : '未知错误';
      setLlmTestResult({ success: false, message: msg });
      toast.error(`测试失败: ${msg}`);
    } finally {
      setTestingLLM(false);
    }
  };

  if (loading || !config) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center space-y-4">
          <div className="loading-spinner mx-auto" />
          <p className="text-muted-foreground text-sm">加载配置中...</p>
        </div>
      </div>
    );
  }

  const currentProvider = LLM_PROVIDERS.find(p => p.value === config.llmProvider);
  const isConfigured = config.llmApiKey !== '' || config.llmProvider === 'ollama';

  return (
    <div className="space-y-6">
      {/* Status Bar */}
      <div className={`rounded-xl border p-4 ${isConfigured ? 'border-emerald-200 bg-emerald-50' : 'border-amber-200 bg-amber-50'}`}>
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center gap-4">
            <Info className="h-5 w-5 text-blue-600" />
            <span className="text-sm">
              {isConfigured ? (
                <span className="text-emerald-700 flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4" /> LLM 已配置 ({currentProvider?.label})
                </span>
              ) : (
                <span className="text-amber-700 flex items-center gap-2">
                  <AlertCircle className="h-4 w-4" /> 请配置 LLM API Key
                </span>
              )}
            </span>
          </div>
          <div className="flex gap-2">
            {hasChanges && (
              <Button onClick={saveConfig} size="sm" className="h-8">
                <Save className="w-3 h-3 mr-2" /> 保存
              </Button>
            )}
            <Button onClick={resetConfig} variant="outline" size="sm" className="h-8">
              <RotateCcw className="w-3 h-3 mr-2" /> 重置
            </Button>
          </div>
        </div>
      </div>

      <Tabs defaultValue="llm" className="w-full">
        <TabsList className="grid w-full grid-cols-5 bg-muted border border-border p-1 h-auto gap-1 rounded-lg mb-6">
          <TabsTrigger value="llm" className="data-[state=active]:bg-background data-[state=active]:text-foreground py-2.5 text-muted-foreground transition-all rounded-md text-sm flex items-center gap-2">
            <Zap className="w-3 h-3" /> LLM 配置
          </TabsTrigger>
          <TabsTrigger value="embedding" className="data-[state=active]:bg-background data-[state=active]:text-foreground py-2.5 text-muted-foreground transition-all rounded-md text-sm flex items-center gap-2">
            <Brain className="w-3 h-3" /> 嵌入模型
          </TabsTrigger>
          <TabsTrigger value="analysis" className="data-[state=active]:bg-background data-[state=active]:text-foreground py-2.5 text-muted-foreground transition-all rounded-md text-sm flex items-center gap-2">
            <Settings className="w-3 h-3" /> 分析参数
          </TabsTrigger>
          <TabsTrigger value="git" className="data-[state=active]:bg-background data-[state=active]:text-foreground py-2.5 text-muted-foreground transition-all rounded-md text-sm flex items-center gap-2">
            <Globe className="w-3 h-3" /> Git 集成
          </TabsTrigger>
            <TabsTrigger value="sandbox" className="data-[state=active]:bg-background data-[state=active]:text-foreground py-2.5 text-muted-foreground transition-all rounded-md text-sm flex items-center gap-2">
              <Shield className="w-4 h-4" /> Sandbox
            </TabsTrigger>
        </TabsList>

        {/* LLM Config */}
        <TabsContent value="llm" className="space-y-6">
          <SectionPanel>
            <div className="space-y-6">
            {/* Provider Selection */}
            <div className="space-y-2">
              <Label className="text-sm font-medium">提供商类型</Label>
              <Select value={config.llmProvider} onValueChange={(v) => updateConfig('llmProvider', v)}>
                <SelectTrigger className="h-12">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LLM_PROVIDERS.map(p => (
                    <SelectItem key={p.value} value={p.value}>
                      {p.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* API Key */}
            {config.llmProvider !== 'ollama' && (
              <div className="space-y-2">
                <Label className="text-xs font-bold text-muted-foreground uppercase">API Key</Label>
                <div className="flex gap-2">
                  <Input
                    type={showApiKey ? 'text' : 'password'}
                    value={config.llmApiKey}
                    onChange={(e) => updateConfig('llmApiKey', e.target.value)}
                    placeholder={config.llmProvider === 'baidu' ? 'API_KEY:SECRET_KEY 格式' : '输入你的 API Key'}
                    className="h-12"
                  />
                  <Button
                    variant="outline"
                    size="icon"
                    onClick={() => setShowApiKey(!showApiKey)}
                    className="h-12 w-12"
                  >
                    {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </Button>
                </div>
              </div>
            )}

            {/* Model and Base URL */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label className="text-xs font-bold text-muted-foreground uppercase">模型名称</Label>
                <Input
                  value={config.llmModel}
                  onChange={(e) => updateConfig('llmModel', e.target.value)}
                  placeholder={`默认: ${DEFAULT_MODELS[config.llmProvider] || 'auto'}`}
                  className="h-10"
                />
              </div>
              <div className="space-y-2">
                <Label className="text-xs font-bold text-muted-foreground uppercase">API Base URL <span className="text-red-400">*</span></Label>
                <Input
                  value={config.llmBaseUrl}
                  onChange={(e) => updateConfig('llmBaseUrl', e.target.value)}
                  placeholder="https://api.openai.com"
                  required
                  className="h-10"
                />
              </div>
            </div>

            {/* Test Connection */}
            <div className="pt-4 border-t border-border border-dashed flex items-center justify-between flex-wrap gap-4">
              <div className="text-sm">
                <span className="font-bold text-foreground">测试连接</span>
                <span className="text-muted-foreground ml-2">验证配置是否正确</span>
              </div>
              <Button
                onClick={testLLMConnection}
                disabled={testingLLM || (!isConfigured && config.llmProvider !== 'ollama')}
                className="h-10"
              >
                {testingLLM ? (
                  <>
                    <div className="loading-spinner w-4 h-4 mr-2" />
                    测试中...
                  </>
                ) : (
                  <>
                    <PlayCircle className="w-4 h-4 mr-2" />
                    测试
                  </>
                )}
              </Button>
            </div>
            {llmTestResult && (
              <div className={`p-3 rounded-lg ${llmTestResult.success ? 'bg-emerald-500/10 border border-emerald-500/30' : 'bg-rose-500/10 border border-rose-500/30'}`}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm">
                    {llmTestResult.success ? (
                      <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                    ) : (
                      <AlertCircle className="h-4 w-4 text-rose-400" />
                    )}
                    <span className={llmTestResult.success ? 'text-emerald-300/80' : 'text-rose-300/80'}>
                      {llmTestResult.message}
                    </span>
                  </div>
                  {llmTestResult.debug && (
                    <button
                      onClick={() => setShowDebugInfo(!showDebugInfo)}
                      className="text-xs text-muted-foreground hover:text-foreground underline"
                    >
                      {showDebugInfo ? '隐藏调试信息' : '显示调试信息'}
                    </button>
                  )}
                </div>
                {showDebugInfo && llmTestResult.debug && (
                  <div className="mt-3 pt-3 border-t border-border/50">
                    <div className="text-xs font-mono space-y-1 text-muted-foreground">
                      <div className="font-bold text-foreground mb-2">连接信息:</div>
                      <div>Provider: <span className="text-foreground">{String(llmTestResult.debug.provider)}</span></div>
                      <div>Model: <span className="text-foreground">{String(llmTestResult.debug.model_used || llmTestResult.debug.model_requested || 'N/A')}</span></div>
                      <div>Base URL: <span className="text-foreground">{String(llmTestResult.debug.base_url_used || llmTestResult.debug.base_url_requested || '(default)')}</span></div>
                      <div>Adapter: <span className="text-foreground">{String(llmTestResult.debug.adapter_type || 'N/A')}</span></div>
                      <div>API Key: <span className="text-foreground">{String(llmTestResult.debug.api_key_prefix)} (长度: {String(llmTestResult.debug.api_key_length)})</span></div>
                      <div>耗时: <span className="text-foreground">{String(llmTestResult.debug.elapsed_time_ms || 'N/A')} ms</span></div>

                      {/* 用户保存的配置参数 */}
                      {llmTestResult.debug.saved_config && (
                        <div className="mt-3 pt-2 border-t border-border/30">
                          <div className="font-bold text-cyan-400 mb-2">已保存的配置参数:</div>
                          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                            <div>温度: <span className="text-foreground">{String((llmTestResult.debug.saved_config as Record<string, unknown>).temperature ?? 'N/A')}</span></div>
                            <div>最大Tokens: <span className="text-foreground">{String((llmTestResult.debug.saved_config as Record<string, unknown>).max_tokens ?? 'N/A')}</span></div>
                            <div>超时: <span className="text-foreground">{String((llmTestResult.debug.saved_config as Record<string, unknown>).timeout_ms ?? 'N/A')} ms</span></div>
                            <div>请求间隔: <span className="text-foreground">{String((llmTestResult.debug.saved_config as Record<string, unknown>).gap_ms ?? 'N/A')} ms</span></div>
                            <div>每分钟请求数: <span className="text-foreground">{String((llmTestResult.debug.saved_config as Record<string, unknown>).llm_rate_per_minute ?? 'N/A')}</span></div>
                            <div>并发数: <span className="text-foreground">{String((llmTestResult.debug.saved_config as Record<string, unknown>).concurrency ?? 'N/A')}</span></div>
                            <div>最大文件数: <span className="text-foreground">{String((llmTestResult.debug.saved_config as Record<string, unknown>).max_analyze_files ?? 'N/A')}</span></div>
                            <div>输出语言: <span className="text-foreground">{String((llmTestResult.debug.saved_config as Record<string, unknown>).output_language ?? 'N/A')}</span></div>
                          </div>
                        </div>
                      )}

                      {/* 测试时实际使用的参数 */}
                      {llmTestResult.debug.test_params && (
                        <div className="mt-2 pt-2 border-t border-border/30">
                          <div className="font-bold text-emerald-400 mb-2">测试时使用的参数:</div>
                          <div className="grid grid-cols-3 gap-x-4">
                            <div>温度: <span className="text-foreground">{String((llmTestResult.debug.test_params as Record<string, unknown>).temperature ?? 'N/A')}</span></div>
                            <div>超时: <span className="text-foreground">{String((llmTestResult.debug.test_params as Record<string, unknown>).timeout ?? 'N/A')}s</span></div>
                            <div>MaxTokens: <span className="text-foreground">{String((llmTestResult.debug.test_params as Record<string, unknown>).max_tokens ?? 'N/A')}</span></div>
                          </div>
                        </div>
                      )}

                      {llmTestResult.debug.error_category && (
                        <div className="mt-2">错误类型: <span className="text-rose-400">{String(llmTestResult.debug.error_category)}</span></div>
                      )}
                      {llmTestResult.debug.error_type && (
                        <div>异常类型: <span className="text-rose-400">{String(llmTestResult.debug.error_type)}</span></div>
                      )}
                      {llmTestResult.debug.status_code && (
                        <div>HTTP 状态码: <span className="text-rose-400">{String(llmTestResult.debug.status_code)}</span></div>
                      )}
                      {llmTestResult.debug.api_response && (
                        <div className="mt-2">
                          <div className="font-bold text-amber-400">API 服务器返回:</div>
                          <pre className="mt-1 p-2 bg-amber-500/10 border border-amber-500/30 rounded text-xs overflow-x-auto">
                            {String(llmTestResult.debug.api_response)}
                          </pre>
                        </div>
                      )}
                      {llmTestResult.debug.error_message && (
                        <div className="mt-2">
                          <div className="font-bold text-foreground">完整错误信息:</div>
                          <pre className="mt-1 p-2 bg-background/50 rounded text-xs overflow-x-auto max-h-32 overflow-y-auto">
                            {String(llmTestResult.debug.error_message)}
                          </pre>
                        </div>
                      )}
                      {llmTestResult.debug.traceback && (
                        <details className="mt-2">
                          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">完整堆栈跟踪</summary>
                          <pre className="mt-1 p-2 bg-background/50 rounded text-xs overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap">
                            {String(llmTestResult.debug.traceback)}
                          </pre>
                        </details>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Advanced Parameters */}
            <details open className="pt-4 border-t border-border border-dashed">
              <summary className="font-bold uppercase cursor-pointer hover:text-primary text-muted-foreground text-sm">高级参数</summary>

              {/* LLM基础参数 */}
              <div className="mt-4 mb-2">
                <span className="text-xs text-muted-foreground uppercase font-semibold">LLM 基础参数</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground uppercase">请求超时 (毫秒)</Label>
                  <Input
                    type="number"
                    value={config.llmTimeout}
                    onChange={(e) => updateConfig('llmTimeout', Number(e.target.value))}
                    className="h-10"
                  />
                  <p className="text-xs text-muted-foreground">单次LLM请求的超时时间</p>
                </div>
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground uppercase">温度 (0-2)</Label>
                  <Input
                    type="number"
                    step="0.1"
                    min="0"
                    max="2"
                    value={config.llmTemperature}
                    onChange={(e) => updateConfig('llmTemperature', Number(e.target.value))}
                    className="h-10"
                  />
                  <p className="text-xs text-muted-foreground">控制输出随机性，越低越确定</p>
                </div>
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground uppercase">最大 Tokens</Label>
                  <Input
                    type="number"
                    value={config.llmMaxTokens}
                    onChange={(e) => updateConfig('llmMaxTokens', Number(e.target.value))}
                    className="h-10"
                  />
                  <p className="text-xs text-muted-foreground">单次请求最大输出Token数</p>
                </div>
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground uppercase">每分钟请求次数 (RPM)</Label>
                  <Input
                    type="number"
                    min="1"
                    value={config.llmRatePerMinute}
                    onChange={(e) => updateConfig('llmRatePerMinute', Math.max(1, Number(e.target.value) || 1))}
                    className="h-10"
                  />
                  <p className="text-xs text-muted-foreground">限制 Agent 对模型的每分钟总请求数，超过后会进入自动限流。</p>
                </div>
              </div>

              {/* Agent超时配置 */}
              <div className="mt-6 mb-2">
                <span className="text-xs text-muted-foreground uppercase font-semibold">Agent 超时配置</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground uppercase">首Token超时 (秒)</Label>
                  <Input
                    type="number"
                    value={config.llmFirstTokenTimeout}
                    onChange={(e) => updateConfig('llmFirstTokenTimeout', Number(e.target.value))}
                    className="h-10"
                  />
                  <p className="text-xs text-muted-foreground">等待LLM首个Token的超时时间</p>
                </div>
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground uppercase">流式超时 (秒)</Label>
                  <Input
                    type="number"
                    value={config.llmStreamTimeout}
                    onChange={(e) => updateConfig('llmStreamTimeout', Number(e.target.value))}
                    className="h-10"
                  />
                  <p className="text-xs text-muted-foreground">流式输出中两个Token间的超时</p>
                </div>
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground uppercase">工具超时 (秒)</Label>
                  <Input
                    type="number"
                    value={config.toolTimeout}
                    onChange={(e) => updateConfig('toolTimeout', Number(e.target.value))}
                    className="h-10"
                  />
                  <p className="text-xs text-muted-foreground">单个工具执行的默认超时时间</p>
                </div>
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground uppercase">子Agent超时 (秒)</Label>
                  <Input
                    type="number"
                    value={config.subAgentTimeout}
                    onChange={(e) => updateConfig('subAgentTimeout', Number(e.target.value))}
                    className="h-10"
                  />
                  <p className="text-xs text-muted-foreground">子Agent (Recon/Analysis/Verification) 超时</p>
                </div>
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground uppercase">总超时 (秒)</Label>
                  <Input
                    type="number"
                    value={config.agentTimeout}
                    onChange={(e) => updateConfig('agentTimeout', Number(e.target.value))}
                    className="h-10"
                  />
                  <p className="text-xs text-muted-foreground">整个Agent审计任务的最大时间</p>
                </div>
              </div>
            </details>
          </div>

          {/* Usage Notes */}
          <div className="bg-muted border border-border p-4 rounded-lg text-xs space-y-2">
            <p className="font-bold uppercase text-muted-foreground flex items-center gap-2">
              <Info className="w-4 h-4 text-sky-400" />
              配置说明
            </p>
            <p className="text-muted-foreground">• <strong className="text-muted-foreground">LiteLLM 统一适配</strong>: 大多数提供商通过 LiteLLM 统一处理，支持自动重试和负载均衡</p>
            <p className="text-muted-foreground">• <strong className="text-muted-foreground">原生适配器</strong>: 百度、MiniMax、豆包因 API 格式特殊，使用专用适配器</p>
            <p className="text-muted-foreground">• <strong className="text-muted-foreground">API 中转站</strong>: 在 Base URL 填入中转站地址即可，API Key 填中转站提供的 Key</p>
          </div>
        </SectionPanel>
        </TabsContent>

        {/* Embedding Config */}
        <TabsContent value="embedding" className="space-y-6">
          <EmbeddingConfig />
        </TabsContent>

        {/* Analysis Parameters */}
        <TabsContent value="analysis" className="space-y-6">
          <SectionPanel className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-2">
                <Label className="text-xs font-bold text-muted-foreground uppercase">最大分析文件数</Label>
                <Input
                  type="number"
                  value={config.maxAnalyzeFiles}
                  onChange={(e) => updateConfig('maxAnalyzeFiles', Number(e.target.value))}
                  className="h-10"
                />
                <p className="text-xs text-muted-foreground">单次任务最多处理的文件数量</p>
              </div>
              <div className="space-y-2">
                <Label className="text-xs font-bold text-muted-foreground uppercase">LLM 并发数</Label>
                <Input
                  type="number"
                  value={config.llmConcurrency}
                  onChange={(e) => updateConfig('llmConcurrency', Number(e.target.value))}
                  className="h-10"
                />
                <p className="text-xs text-muted-foreground">同时发送的 LLM 请求数量</p>
              </div>
              <div className="space-y-2">
                <Label className="text-xs font-bold text-muted-foreground uppercase">请求间隔 (毫秒)</Label>
                <Input
                  type="number"
                  value={config.llmGapMs}
                  onChange={(e) => updateConfig('llmGapMs', Number(e.target.value))}
                  className="h-10"
                />
                <p className="text-xs text-muted-foreground">每个请求之间的延迟时间</p>
              </div>
              <div className="space-y-2">
                <Label className="text-xs font-bold text-muted-foreground uppercase">输出语言</Label>
                <Select value={config.outputLanguage} onValueChange={(v) => updateConfig('outputLanguage', v)}>
                  <SelectTrigger className="h-10">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="zh-CN" className="font-mono">🇨🇳 中文</SelectItem>
                    <SelectItem value="en-US" className="font-mono">🇺🇸 English</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">代码审查结果的输出语言</p>
              </div>
            </div>
          </SectionPanel>
        </TabsContent>

        {/* Git Integration */}
        <TabsContent value="git" className="space-y-6">
          <SectionPanel className="space-y-6">
            <div className="space-y-2">
              <Label className="text-xs font-bold text-muted-foreground uppercase">GitHub Token (可选)</Label>
              <Input
                type="password"
                value={config.githubToken}
                onChange={(e) => updateConfig('githubToken', e.target.value)}
                placeholder="ghp_xxxxxxxxxxxx"
                className="h-10"
              />
              <p className="text-xs text-muted-foreground">
                用于访问私有仓库。获取:{' '}
                <a href="https://github.com/settings/tokens" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
                  github.com/settings/tokens
                </a>
              </p>
            </div>
            <div className="space-y-2">
              <Label className="text-xs font-bold text-muted-foreground uppercase">GitLab Token (可选)</Label>
              <Input
                type="password"
                value={config.gitlabToken}
                onChange={(e) => updateConfig('gitlabToken', e.target.value)}
                placeholder="glpat-xxxxxxxxxxxx"
                className="h-10"
              />
              <p className="text-xs text-muted-foreground">
                用于访问私有仓库。获取:{' '}
                <a href="https://gitlab.com/-/profile/personal_access_tokens" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
                  gitlab.com/-/profile/personal_access_tokens
                </a>
              </p>
            </div>
            <div className="space-y-2">
              <Label className="text-xs font-bold text-muted-foreground uppercase">Gitea Token (可选)</Label>
              <Input
                type="password"
                value={config.giteaToken}
                onChange={(e) => updateConfig('giteaToken', e.target.value)}
                placeholder="sha1_xxxxxxxxxxxx"
                className="h-10"
              />
              <p className="text-xs text-muted-foreground">
                用于访问 Gitea 私有仓库。获取:{' '}
                <span className="text-primary">
                  [your-gitea-instance]/user/settings/applications
                </span>
              </p>
            </div>
            <div className="bg-muted border border-border p-4 rounded-lg text-xs">
              <p className="font-bold text-muted-foreground flex items-center gap-2 mb-2">
                <Info className="w-4 h-4 text-sky-400" />
                提示
              </p>
              <p className="text-muted-foreground">• 公开仓库无需配置 Token</p>
              <p className="text-muted-foreground">• 私有仓库需要配置对应平台的 Token</p>
            </div>
          </SectionPanel>

          {/* SSH Key Management */}
          <SectionPanel className="space-y-4">
            <div className="flex items-center gap-3 mb-2">
              <Key className="w-5 h-5 text-emerald-600" />
              <h3 className="text-lg font-semibold text-foreground">SSH 密钥管理</h3>
            </div>

            <div className="flex items-start gap-3 p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-lg">
              <div className="flex-shrink-0 mt-0.5">
                <div className="w-8 h-8 rounded-lg bg-emerald-500/20 flex items-center justify-center">
                  <Key className="w-4 h-4 text-emerald-400" />
                </div>
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm text-foreground font-medium mb-1">
                  使用 SSH 密钥访问 Git 仓库
                </p>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  生成 SSH 密钥对后，将公钥添加到 GitHub/GitLab，即可使用 SSH URL 访问私有仓库。私钥将被加密存储。
                </p>
              </div>
            </div>

            {!sshKey.has_key ? (
              <div className="text-center py-8">
                <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-muted/50 mb-4">
                  <Key className="w-8 h-8 text-muted-foreground" />
                </div>
                <p className="text-sm text-muted-foreground mb-4">尚未生成 SSH 密钥</p>
                <Button
                  onClick={handleGenerateSSHKey}
                  disabled={generatingKey}
                  className="h-10"
                >
                  {generatingKey ? (
                    <>
                      <div className="loading-spinner w-4 h-4 mr-2" />
                      生成中...
                    </>
                  ) : (
                    <>
                      <Key className="w-4 h-4 mr-2" />
                      生成 SSH 密钥
                    </>
                  )}
                </Button>
              </div>
            ) : (
              <div className="space-y-4">
                {/* Public Key Display */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs font-bold text-muted-foreground uppercase flex items-center gap-2">
                      <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                      SSH 公钥
                    </Label>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={handleCopyPublicKey}
                      className="h-8 text-xs"
                    >
                      <Copy className="w-3 h-3 mr-1" />
                      复制
                    </Button>
                  </div>
                  <Textarea
                    value={sshKey.public_key || ""}
                    readOnly
                    className="font-mono text-xs h-24 resize-none"
                  />

                  {/* 显示指纹 */}
                  {sshKey.fingerprint && (
                    <div className="p-3 bg-muted/50 rounded border border-border">
                      <Label className="text-xs font-bold text-muted-foreground uppercase mb-1 block">
                        公钥指纹 (SHA256)
                      </Label>
                      <code className="text-xs text-emerald-400 font-mono break-all">
                        {sshKey.fingerprint}
                      </code>
                    </div>
                  )}

                  <p className="text-xs text-muted-foreground">
                    请将此公钥添加到 <a href="https://github.com/settings/keys" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">GitHub</a> 或 <a href="https://gitlab.com/-/profile/keys" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">GitLab</a> 账户
                  </p>
                </div>

                {/* Test SSH Connection */}
                <div className="space-y-2 pt-4 border-t border-border">
                  <Label className="text-xs font-bold text-muted-foreground uppercase">
                    测试 SSH 连接
                  </Label>
                  <div className="flex gap-2">
                    <Input
                      placeholder="git@github.com:username/repo.git"
                      value={testRepoUrl}
                      onChange={(e) => setTestRepoUrl(e.target.value)}
                      className="font-mono text-xs"
                    />
                    <Button
                      onClick={handleTestSSHKey}
                      disabled={testingKey}
                      className="whitespace-nowrap"
                    >
                      {testingKey ? (
                        <>
                          <div className="loading-spinner w-4 h-4 mr-2" />
                          测试中...
                        </>
                      ) : (
                        <>
                          <Terminal className="w-4 h-4 mr-2" />
                          测试连接
                        </>
                      )}
                    </Button>
                  </div>
                </div>

                {/* Delete Key and Clear Known Hosts */}
                <div className="flex justify-end gap-2 pt-4 border-t border-border">
                  <Button
                    variant="outline"
                    onClick={handleClearKnownHosts}
                    disabled={clearingKnownHosts}
                    className="h-10"
                  >
                    {clearingKnownHosts ? (
                      <>
                        <div className="loading-spinner w-4 h-4 mr-2" />
                        清理中...
                      </>
                    ) : (
                      <>
                        <ServerCrash className="w-4 h-4 mr-2" />
                        清理 known_hosts
                      </>
                    )}
                  </Button>
                  <Button
                    variant="destructive"
                    onClick={() => setShowDeleteKeyDialog(true)}
                    className="bg-rose-500/20 hover:bg-rose-500/30 text-rose-400 border border-rose-500/30 h-10"
                  >
                    <Trash2 className="w-4 h-4 mr-2" />
                    删除密钥
                  </Button>
                </div>
              </div>
                )}
          </SectionPanel>
        </TabsContent>

        {/* Sandbox */}
        <TabsContent value="sandbox" className="space-y-6">
          <SectionPanel>
            <div className="space-y-6">
              {/* Sandbox Network Switch */}
              <div className={`relative overflow-hidden rounded-xl border-2 transition-all duration-300 ${
                config.sandboxNetworkEnabled
                  ? 'border-green-400/60 bg-gradient-to-br from-green-50 to-emerald-50 dark:from-green-950/30 dark:to-emerald-950/20 dark:border-green-600/40'
                  : 'border-red-200/60 bg-gradient-to-br from-red-50/50 to-orange-50/30 dark:from-red-950/20 dark:to-orange-950/10 dark:border-red-800/30'
              }`}>
                {/* Decorative background circle */}
                <div className={`absolute -right-6 -top-6 h-24 w-24 rounded-full transition-all duration-500 ${
                  config.sandboxNetworkEnabled
                    ? 'bg-green-400/10 scale-100'
                    : 'bg-red-400/10 scale-100'
                }`} />

                <div className="relative flex items-center justify-between p-5">
                  <div className="flex items-center gap-4">
                    <div className={`flex h-11 w-11 items-center justify-center rounded-xl transition-all duration-300 ${
                      config.sandboxNetworkEnabled
                        ? 'bg-green-500 text-white shadow-lg shadow-green-500/25'
                        : 'bg-red-100 dark:bg-red-950/40 text-red-500 dark:text-red-400 border border-red-200 dark:border-red-800/50'
                    }`}>
                      {config.sandboxNetworkEnabled ? (
                        <Wifi className="h-5 w-5" />
                      ) : (
                        <WifiOff className="h-5 w-5" />
                      )}
                    </div>
                    <div className="space-y-0.5">
                      <div className="flex items-center gap-2">
                        <Label className="text-sm font-semibold cursor-pointer">沙箱联网</Label>
                        <span className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-medium transition-colors duration-300 ${
                          config.sandboxNetworkEnabled
                            ? 'bg-green-500/15 text-green-700 dark:text-green-400'
                            : 'bg-red-500/15 text-red-700 dark:text-red-400'
                        }`}>
                          <span className={`inline-block h-1.5 w-1.5 rounded-full ${
                            config.sandboxNetworkEnabled ? 'bg-green-500 animate-pulse' : 'bg-red-500'
                          }`} />
                          {config.sandboxNetworkEnabled ? '已开启' : '已关闭'}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground leading-relaxed">允许沙箱容器访问外部网络（用于安装依赖等）</p>
                    </div>
                  </div>
                  <Switch
                    checked={config.sandboxNetworkEnabled}
                    onCheckedChange={(v) => updateConfig('sandboxNetworkEnabled', v)}
                    className="data-[state=checked]:bg-green-500 data-[state=unchecked]:bg-gray-300 dark:data-[state=unchecked]:bg-gray-600 data-[state=unchecked]:border-gray-300 dark:data-[state=unchecked]:border-gray-600"
                  />
                </div>
              </div>

              {/* Network Test */}
              <div className="space-y-2">
                <Label className="text-xs font-bold text-muted-foreground uppercase">网络测试</Label>
                <div className="flex gap-2">
                  <Input value={sandboxTestUrl} onChange={(e) => setSandboxTestUrl(e.target.value)} placeholder="测试 URL" className="flex-1" />
                  <Button variant="outline" onClick={testSandboxNetwork} disabled={testingNetwork}>
                    {testingNetwork ? <RotateCcw className="w-4 h-4 animate-spin" /> : '测试连接'}
                  </Button>
                </div>
                {sandboxTestResult && (
                  <div className={sandboxTestResult.success ? 'text-green-600 flex items-center gap-2 text-sm' : 'text-red-600 flex items-center gap-2 text-sm'}>
                    {sandboxTestResult.success ? <CheckCircle2 className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
                    {sandboxTestResult.message}
                  </div>
                )}
              </div>

              {/* Dependency Install Terminal */}
              <div className="space-y-2">
                <Label className="text-xs font-bold text-muted-foreground uppercase">依赖安装</Label>
                <div className="flex gap-2">
                  <Input value={sandboxInstallCmd} onChange={(e) => setSandboxInstallCmd(e.target.value)} placeholder="例如: pip install requests" className="flex-1" />
                  <Button variant="outline" onClick={execSandboxCommand} disabled={executingSandbox || !sandboxInstallCmd.trim()}>
                    {executingSandbox ? <RotateCcw className="w-4 h-4 animate-spin" /> : '执行'}
                  </Button>
                </div>
                {sandboxExecOutput && (
                  <pre className="bg-muted rounded-lg p-3 text-xs font-mono overflow-auto max-h-48 whitespace-pre-wrap">{sandboxExecOutput}</pre>
                )}
              </div>

              {/* Installed Packages */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label className="text-xs font-bold text-muted-foreground uppercase">已安装包</Label>
                  <Button variant="ghost" size="sm" onClick={loadSandboxPackages} disabled={loadingPackages}>
                    {loadingPackages ? <RotateCcw className="w-3 h-3 animate-spin" /> : '刷新'}
                  </Button>
                </div>
                {sandboxPackages.length > 0 ? (
                  <div className="border rounded-lg divide-y max-h-48 overflow-auto">
                    {sandboxPackages.map((pkg, i) => (
                      <div key={i} className="flex justify-between px-3 py-2 text-sm">
                        <span className="font-mono">{pkg.name}</span>
                        <span className="text-muted-foreground">{pkg.version}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">暂无已安装的包</p>
                )}
              </div>
            </div>
          </SectionPanel>
        </TabsContent>

      </Tabs>

      {/* Floating Save Button */}
      {hasChanges && (
        <div className="fixed bottom-6 right-6 bg-card border border-border rounded-xl shadow-lg p-4 z-50">
          <Button onClick={saveConfig} className="h-12">
            <Save className="w-4 h-4 mr-2" /> 保存所有更改
          </Button>
        </div>
      )}

      {/* Delete SSH Key Confirmation Dialog */}
      <AlertDialog open={showDeleteKeyDialog} onOpenChange={setShowDeleteKeyDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="text-lg font-semibold text-foreground flex items-center gap-2">
              <Trash2 className="w-5 h-5 text-red-500" />
              确认删除 SSH 密钥？
            </AlertDialogTitle>
            <AlertDialogDescription className="text-muted-foreground">
              删除后将无法使用 SSH 方式访问 Git 仓库，需要重新生成密钥。此操作不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deletingKey}>
              取消
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteSSHKey}
              disabled={deletingKey}
              className="bg-red-600 hover:bg-red-700 text-white"
            >
              {deletingKey ? (
                <>
                  <div className="loading-spinner w-4 h-4 mr-2" />
                  删除中...
                </>
              ) : (
                "确认删除"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
