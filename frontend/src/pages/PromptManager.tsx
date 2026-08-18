/**
 * Prompt Template Manager Page
 * Enterprise Blue-White UI
 */

import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { toast } from 'sonner';
import { PageHeader } from '@/components/layout/PageHeader';
import { SectionPanel } from '@/components/ui/section-panel';
import {
  Plus,
  Trash2,
  Edit,
  Copy,
  Play,
  FileText,
  Sparkles,
  Check,
  Loader2,
  MessageSquare,
  Shield,
  Code,
  AlertTriangle,
  Activity,
} from 'lucide-react';
import {
  getPromptTemplates,
  createPromptTemplate,
  updatePromptTemplate,
  deletePromptTemplate,
  testPromptTemplate,
  type PromptTemplate,
  type PromptTemplateCreate,
} from '@/shared/api/prompts';
import { TEST_CODE_SAMPLES, TEMPLATE_TEST_CODES } from './prompt-manager/testCodeSamples';

const TEMPLATE_TYPES = [
  { value: 'system', label: '系统提示词' },
  { value: 'user', label: '用户提示词' },
  { value: 'analysis', label: '分析提示词' },
];

const getTemplateIcon = (type: string) => {
  switch (type) {
    case 'system': return Shield;
    case 'user': return MessageSquare;
    case 'analysis': return Code;
    default: return FileText;
  }
};

export default function PromptManager() {
  const [templates, setTemplates] = useState<PromptTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [showEditDialog, setShowEditDialog] = useState(false);
  const [showTestDialog, setShowTestDialog] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState<PromptTemplate | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);
  const [form, setForm] = useState<PromptTemplateCreate>({
    name: '', description: '', template_type: 'system', content_zh: '', content_en: '', is_active: true,
  });
  const [testForm, setTestForm] = useState({ language: 'python', code: TEST_CODE_SAMPLES.python, promptLang: 'zh' as 'zh' | 'en' });
  const [showViewDialog, setShowViewDialog] = useState(false);
  const [viewTemplate, setViewTemplate] = useState<PromptTemplate | null>(null);

  useEffect(() => { loadTemplates(); }, []);

  const loadTemplates = async () => {
    try {
      setLoading(true);
      const response = await getPromptTemplates();
      setTemplates(response.items);
    } catch (error) {
      toast.error('加载提示词模板失败');
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    try {
      await createPromptTemplate(form);
      toast.success('创建成功');
      setShowCreateDialog(false);
      resetForm();
      loadTemplates();
    } catch (error) { toast.error('创建失败'); }
  };

  const handleUpdate = async () => {
    if (!selectedTemplate) return;
    try {
      await updatePromptTemplate(selectedTemplate.id, form);
      toast.success('更新成功');
      setShowEditDialog(false);
      loadTemplates();
    } catch (error) { toast.error('更新失败'); }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('确定要删除此模板吗？')) return;
    try {
      await deletePromptTemplate(id);
      toast.success('删除成功');
      loadTemplates();
    } catch (error: any) { toast.error(error.message || '删除失败'); }
  };

  const handleTest = async () => {
    if (!selectedTemplate) return;
    const content = testForm.promptLang === 'zh'
      ? (selectedTemplate.content_zh || selectedTemplate.content_en || '')
      : (selectedTemplate.content_en || selectedTemplate.content_zh || '');
    if (!content) { toast.error('提示词内容为空'); return; }
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testPromptTemplate({ content, language: testForm.language, code: testForm.code, output_language: testForm.promptLang });
      setTestResult(result);
      if (result.success) toast.success(`测试完成，耗时 ${result.execution_time}s`);
      else toast.error(result.error || '测试失败');
    } catch (error: any) { toast.error(error.message || '测试失败'); }
    finally { setTesting(false); }
  };

  const resetForm = () => {
    setForm({ name: '', description: '', template_type: 'system', content_zh: '', content_en: '', is_active: true });
  };

  const openEditDialog = (template: PromptTemplate) => {
    setSelectedTemplate(template);
    setForm({ name: template.name, description: template.description || '', template_type: template.template_type, content_zh: template.content_zh || '', content_en: template.content_en || '', is_active: template.is_active });
    setShowEditDialog(true);
  };

  const openTestDialog = (template: PromptTemplate) => {
    setSelectedTemplate(template);
    setTestResult(null);

    const templateCodes = TEMPLATE_TEST_CODES[template.name];
    const defaultLang = 'python';
    if (templateCodes && templateCodes[defaultLang]) {
      setTestForm(prev => ({
        ...prev,
        language: defaultLang,
        code: templateCodes[defaultLang]
      }));
    } else {
      setTestForm(prev => ({
        ...prev,
        language: defaultLang,
        code: TEST_CODE_SAMPLES[defaultLang]
      }));
    }

    setShowTestDialog(true);
  };

  const openViewDialog = (template: PromptTemplate) => {
    setViewTemplate(template);
    setShowViewDialog(true);
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success('已复制到剪贴板');
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center space-y-4">
          <div className="loading-spinner mx-auto" />
          <p className="text-muted-foreground text-sm">加载中...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6 min-h-screen">
      <PageHeader
        eyebrow="提示词管理"
        title="提示词管理"
        description="维护 Agent 提示词模板和版本。"
      />

      {/* Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="rounded-xl border border-border bg-card shadow-card p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-muted-foreground">模板总数</p>
              <p className="text-2xl font-semibold text-foreground">{templates.length}</p>
            </div>
            <div className="text-primary">
              <FileText className="w-6 h-6" />
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card shadow-card p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-muted-foreground">系统模板</p>
              <p className="text-2xl font-semibold text-sky-600">{templates.filter(t => t.is_system).length}</p>
            </div>
            <div className="text-sky-600">
              <Shield className="w-6 h-6" />
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card shadow-card p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-muted-foreground">自定义模板</p>
              <p className="text-2xl font-semibold text-emerald-600">{templates.filter(t => !t.is_system).length}</p>
            </div>
            <div className="text-emerald-600">
              <Sparkles className="w-6 h-6" />
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card shadow-card p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-muted-foreground">已启用</p>
              <p className="text-2xl font-semibold text-amber-600">{templates.filter(t => t.is_active).length}</p>
            </div>
            <div className="text-amber-600">
              <Activity className="w-6 h-6" />
            </div>
          </div>
        </div>
      </div>

      {/* Action Bar */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-foreground">模板列表</h2>
        <Button onClick={() => { resetForm(); setShowCreateDialog(true); }}>
          <Plus className="w-4 h-4 mr-2" />
          新建模板
        </Button>
      </div>

      {/* Templates Grid */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {templates.length === 0 ? (
          <div className="col-span-full">
            <SectionPanel>
              <div className="flex flex-col items-center py-12 text-center">
                <FileText className="w-12 h-12 text-muted-foreground/40 mb-4" />
                <p className="text-base font-medium text-foreground">暂无提示词模板</p>
                <p className="text-sm text-muted-foreground mt-1">点击"新建模板"创建自定义提示词</p>
                <Button className="mt-4" onClick={() => { resetForm(); setShowCreateDialog(true); }}>
                  <Plus className="w-4 h-4 mr-2" />
                  创建模板
                </Button>
              </div>
            </SectionPanel>
          </div>
        ) : (
          templates.map(template => {
            const TemplateIcon = getTemplateIcon(template.template_type);
            return (
              <SectionPanel key={template.id} className={!template.is_active ? 'opacity-60' : ''}>
                {/* Template Header */}
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 bg-muted border border-border flex items-center justify-center rounded-lg">
                      <TemplateIcon className="w-5 h-5 text-muted-foreground" />
                    </div>
                    <div>
                      <h3 className="font-semibold text-base text-foreground">{template.name}</h3>
                      <p className="text-xs text-muted-foreground line-clamp-1">{template.description}</p>
                    </div>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2 mb-3">
                  {template.is_system && <Badge variant="outline" className="border-blue-200 bg-blue-50 text-blue-700">系统</Badge>}
                  {template.is_default && <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">默认</Badge>}
                  <Badge variant="outline">{TEMPLATE_TYPES.find(t => t.value === template.template_type)?.label}</Badge>
                </div>

                {/* Template Content Preview */}
                <div
                  className="text-xs text-muted-foreground line-clamp-3 bg-muted/50 p-3 border border-border mb-4 cursor-pointer hover:border-border/80 transition-colors rounded-lg"
                  onClick={() => openViewDialog(template)}
                  title="点击查看完整内容"
                >
                  {template.content_zh || template.content_en || '(无内容)'}
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex gap-1">
                    <Button variant="ghost" size="sm" onClick={() => openViewDialog(template)} className="h-8 px-2">
                      <FileText className="w-4 h-4 mr-1" />
                      查看
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => openTestDialog(template)} className="h-8 px-2">
                      <Play className="w-4 h-4 mr-1" />
                      测试
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => copyToClipboard(template.content_zh || template.content_en || '')} className="h-8 px-2">
                      <Copy className="w-4 h-4 mr-1" />
                      复制
                    </Button>
                  </div>
                  <div className="flex gap-1">
                    {!template.is_system && (
                      <>
                        <Button variant="ghost" size="icon" onClick={() => openEditDialog(template)} className="h-8 w-8">
                          <Edit className="w-4 h-4" />
                        </Button>
                        <Button variant="ghost" size="icon" onClick={() => handleDelete(template.id)} className="h-8 w-8 hover:bg-red-50 hover:text-red-600">
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </>
                    )}
                  </div>
                </div>
              </SectionPanel>
            );
          })
        )}
      </div>

      {/* Create/Edit Dialog */}
      <Dialog open={showCreateDialog || showEditDialog} onOpenChange={(open) => { if (!open) { setShowCreateDialog(false); setShowEditDialog(false); } }}>
        <DialogContent className="!w-[min(90vw,700px)] !max-w-none max-h-[85vh] flex flex-col p-0 gap-0">
          <DialogHeader className="px-6 py-4 border-b border-border flex-shrink-0">
            <DialogTitle className="flex items-center gap-3 text-foreground">
              <div className="p-2 bg-primary/10 rounded-lg">
                <FileText className="w-5 h-5 text-primary" />
              </div>
              <div>
                <span className="text-base font-semibold">
                  {showEditDialog ? '编辑模板' : '新建模板'}
                </span>
              </div>
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label className="text-sm font-medium">模板名称 *</Label>
                <Input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="如：安全专项审计" />
              </div>
              <div className="space-y-2">
                <Label className="text-sm font-medium">模板类型</Label>
                <Select value={form.template_type} onValueChange={v => setForm({ ...form, template_type: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {TEMPLATE_TYPES.map(t => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">描述</Label>
              <Input value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="模板用途描述" />
            </div>
            <Tabs defaultValue="zh" className="w-full">
              <TabsList className="grid w-full grid-cols-2 bg-muted border border-border p-1 h-auto gap-1 rounded-lg">
                <TabsTrigger value="zh" className="data-[state=active]:bg-background data-[state=active]:text-foreground py-2 text-muted-foreground transition-all rounded-md text-sm">
                  中文提示词
                </TabsTrigger>
                <TabsTrigger value="en" className="data-[state=active]:bg-background data-[state=active]:text-foreground py-2 text-muted-foreground transition-all rounded-md text-sm">
                  英文提示词
                </TabsTrigger>
              </TabsList>
              <TabsContent value="zh" className="mt-4">
                <Textarea value={form.content_zh} onChange={e => setForm({ ...form, content_zh: e.target.value })} placeholder="输入中文提示词内容..." rows={12} className="text-sm" />
              </TabsContent>
              <TabsContent value="en" className="mt-4">
                <Textarea value={form.content_en} onChange={e => setForm({ ...form, content_en: e.target.value })} placeholder="Enter English prompt content..." rows={12} className="text-sm" />
              </TabsContent>
            </Tabs>
            <div className="flex items-center gap-2">
              <Switch checked={form.is_active} onCheckedChange={v => setForm({ ...form, is_active: v })} />
              <Label className="text-sm font-medium">启用此模板</Label>
            </div>
          </div>
          <DialogFooter className="flex-shrink-0 flex justify-end gap-3 px-6 py-4 border-t border-border">
            <Button variant="outline" onClick={() => { setShowCreateDialog(false); setShowEditDialog(false); }}>取消</Button>
            <Button onClick={showEditDialog ? handleUpdate : handleCreate}>{showEditDialog ? '保存' : '创建'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Test Dialog */}
      <Dialog open={showTestDialog} onOpenChange={setShowTestDialog}>
        <DialogContent className="!w-[min(95vw,1200px)] !max-w-none max-h-[85vh] flex flex-col p-0 gap-0">
          <DialogHeader className="px-6 py-4 border-b border-border flex-shrink-0">
            <DialogTitle className="flex items-center gap-3 text-foreground">
              <div className="p-2 bg-violet-50 rounded-lg">
                <Sparkles className="w-5 h-5 text-violet-600" />
              </div>
              <div>
                <span className="text-base font-semibold">
                  测试提示词: {selectedTemplate?.name}
                </span>
                <p className="text-xs text-muted-foreground font-normal mt-0.5">使用示例代码测试提示词效果</p>
              </div>
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto p-6 grid grid-cols-2 gap-6">
            {/* Left: Input */}
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-2">
                  <Label className="text-sm font-medium">编程语言</Label>
                  <Select value={testForm.language} onValueChange={v => {
                    const templateCodes = selectedTemplate ? TEMPLATE_TEST_CODES[selectedTemplate.name] : null;
                    const code = templateCodes?.[v] || TEST_CODE_SAMPLES[v] || TEST_CODE_SAMPLES.python;
                    setTestForm({ ...testForm, language: v, code });
                  }}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="python">Python</SelectItem>
                      <SelectItem value="javascript">JavaScript</SelectItem>
                      <SelectItem value="java">Java</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label className="text-sm font-medium">提示词语言</Label>
                  <Select value={testForm.promptLang} onValueChange={(v: 'zh' | 'en') => setTestForm({ ...testForm, promptLang: v })}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="zh">中文提示词</SelectItem>
                      <SelectItem value="en">英文提示词</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="space-y-2">
                <Label className="text-sm font-medium">测试代码</Label>
                <Textarea value={testForm.code} onChange={e => setTestForm({ ...testForm, code: e.target.value })} rows={10} className="text-sm" />
              </div>
              <Button onClick={handleTest} disabled={testing} className="w-full h-12">
                {testing ? (<><Loader2 className="w-4 h-4 mr-2 animate-spin" />分析中...</>) : (<><Play className="w-4 h-4 mr-2" />运行测试</>)}
              </Button>
            </div>
            {/* Right: Results */}
            <div className="space-y-4">
              <Label className="text-sm font-medium">分析结果</Label>
              <div className="border border-border h-[400px] overflow-auto bg-muted/20 rounded-lg">
                {testResult ? (
                  testResult.success ? (
                    <div className="flex flex-col h-full">
                      <div className="flex items-center justify-between p-3 bg-emerald-50 border-b border-emerald-200">
                        <div className="flex items-center gap-2 text-emerald-700 font-semibold">
                          <Check className="w-5 h-5" />
                          <span className="text-sm">分析成功</span>
                        </div>
                        <Badge variant="outline" className="text-xs">
                          {testResult.execution_time}s
                        </Badge>
                      </div>

                      {testResult.result?.quality_score !== undefined && (
                        <div className="p-3 bg-muted/50 border-b border-border flex items-center justify-between">
                          <span className="text-sm font-medium text-foreground">质量评分</span>
                          <div className="flex items-center gap-2">
                            <div className={`text-2xl font-bold ${testResult.result.quality_score >= 80 ? 'text-emerald-600' :
                              testResult.result.quality_score >= 60 ? 'text-amber-600' : 'text-red-600'
                              }`}>
                              {testResult.result.quality_score}
                            </div>
                            <span className="text-xs text-muted-foreground">/ 100</span>
                          </div>
                        </div>
                      )}

                      <ScrollArea className="flex-1 p-3">
                        {testResult.result?.issues?.length > 0 ? (
                          <div className="space-y-3">
                            <div className="flex items-center justify-between mb-2">
                              <span className="text-sm font-medium text-foreground">发现问题</span>
                              <Badge variant="outline" className="border-red-200 bg-red-50 text-red-700">
                                {testResult.result.issues.length} 个
                              </Badge>
                            </div>
                            {testResult.result.issues.map((issue: any, idx: number) => (
                              <div key={idx} className="rounded-lg border border-border overflow-hidden">
                                <div className={`px-3 py-2 border-b border-border flex items-center justify-between ${issue.severity === 'critical' ? 'bg-red-50 text-red-700' :
                                  issue.severity === 'high' ? 'bg-orange-50 text-orange-700' :
                                    issue.severity === 'medium' ? 'bg-amber-50 text-amber-700' : 'bg-blue-50 text-blue-700'
                                  }`}>
                                  <span className="font-semibold text-xs">{issue.severity}</span>
                                  {issue.line && <span className="text-xs opacity-80">行 {issue.line}</span>}
                                </div>
                                <div className="p-3">
                                  <h4 className="font-semibold text-sm mb-1 text-foreground">{issue.title}</h4>
                                  {issue.description && (
                                    <p className="text-xs text-muted-foreground leading-relaxed">{issue.description}</p>
                                  )}
                                  {issue.suggestion && (
                                    <div className="mt-2 p-2 bg-blue-50 border-l-2 border-blue-500 rounded-r">
                                      <p className="text-xs text-blue-700">
                                        <span className="font-semibold">建议: </span>
                                        {issue.suggestion}
                                      </p>
                                    </div>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="text-center py-8">
                            <div className="w-12 h-12 bg-emerald-50 border border-emerald-200 flex items-center justify-center mx-auto mb-3 rounded-lg">
                              <Check className="w-6 h-6 text-emerald-600" />
                            </div>
                            <p className="font-semibold text-emerald-600 text-sm">未发现问题</p>
                            <p className="text-xs text-muted-foreground mt-1">代码质量良好</p>
                          </div>
                        )}
                      </ScrollArea>
                    </div>
                  ) : (
                    <div className="flex flex-col h-full">
                      <div className="flex items-center justify-between p-3 bg-red-50 border-b border-red-200">
                        <div className="flex items-center gap-2 text-red-700 font-semibold">
                          <AlertTriangle className="w-5 h-5" />
                          <span className="text-sm">测试失败</span>
                        </div>
                        {testResult.execution_time && (
                          <Badge variant="outline" className="text-xs">
                            {testResult.execution_time}s
                          </Badge>
                        )}
                      </div>
                      <div className="flex-1 p-4">
                        <div className="bg-red-50 border border-red-200 p-4 h-full overflow-auto rounded-lg">
                          <pre className="text-sm text-red-700 whitespace-pre-wrap break-words">
                            {testResult.error || '未知错误'}
                          </pre>
                        </div>
                      </div>
                    </div>
                  )
                ) : (
                  <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
                    <div className="w-16 h-16 bg-muted border border-border flex items-center justify-center mb-4 rounded-lg">
                      <Play className="w-8 h-8 opacity-50" />
                    </div>
                    <p className="text-sm">点击"运行测试"</p>
                    <p className="text-xs mt-1">查看分析结果</p>
                  </div>
                )}
              </div>
            </div>
          </div>
          <DialogFooter className="flex-shrink-0 flex justify-end gap-3 px-6 py-4 border-t border-border">
            <Button variant="outline" onClick={() => setShowTestDialog(false)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* View Dialog */}
      <Dialog open={showViewDialog} onOpenChange={setShowViewDialog}>
        <DialogContent className="!w-[min(90vw,800px)] !max-w-none max-h-[85vh] flex flex-col p-0 gap-0">
          <DialogHeader className="px-6 py-4 border-b border-border flex-shrink-0">
            <DialogTitle className="flex items-center gap-3 text-foreground">
              <div className="p-2 bg-primary/10 rounded-lg">
                <FileText className="w-5 h-5 text-primary" />
              </div>
              <div>
                <span className="text-base font-semibold">
                  {viewTemplate?.name}
                </span>
                <p className="text-xs text-muted-foreground font-normal mt-0.5">{viewTemplate?.description || ''}</p>
              </div>
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            <div className="flex flex-wrap gap-2 mb-4">
              {viewTemplate?.is_system && <Badge variant="outline" className="border-blue-200 bg-blue-50 text-blue-700">系统模板</Badge>}
              {viewTemplate?.is_default && <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">默认</Badge>}
              <Badge variant="outline">{TEMPLATE_TYPES.find(t => t.value === viewTemplate?.template_type)?.label}</Badge>
              {viewTemplate?.is_active ? (
                <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">已启用</Badge>
              ) : (
                <Badge variant="outline">已禁用</Badge>
              )}
            </div>

            <Tabs defaultValue="zh" className="w-full">
              <TabsList className="grid w-full grid-cols-2 bg-muted border border-border p-1 h-auto gap-1 rounded-lg">
                <TabsTrigger value="zh" className="data-[state=active]:bg-background data-[state=active]:text-foreground py-2 text-muted-foreground transition-all rounded-md text-sm">
                  中文提示词
                </TabsTrigger>
                <TabsTrigger value="en" className="data-[state=active]:bg-background data-[state=active]:text-foreground py-2 text-muted-foreground transition-all rounded-md text-sm">
                  英文提示词
                </TabsTrigger>
              </TabsList>
              <TabsContent value="zh" className="mt-4">
                <div className="bg-muted/50 text-foreground p-4 border border-border text-sm whitespace-pre-wrap max-h-[500px] overflow-y-auto rounded-lg">
                  {viewTemplate?.content_zh || '(无中文内容)'}
                </div>
              </TabsContent>
              <TabsContent value="en" className="mt-4">
                <div className="bg-muted/50 text-foreground p-4 border border-border text-sm whitespace-pre-wrap max-h-[500px] overflow-y-auto rounded-lg">
                  {viewTemplate?.content_en || '(No English content)'}
                </div>
              </TabsContent>
            </Tabs>
          </div>
          <DialogFooter className="flex-shrink-0 flex justify-end gap-3 px-6 py-4 border-t border-border">
            <Button variant="outline" onClick={() => setShowViewDialog(false)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* View Dialog */}
      <Dialog open={showViewDialog} onOpenChange={setShowViewDialog}>
        <DialogContent className="!w-[min(90vw,800px)] !max-w-none max-h-[85vh] flex flex-col p-0 gap-0">
          <DialogHeader className="px-6 py-4 border-b border-border flex-shrink-0">
            <DialogTitle className="flex items-center gap-3 text-foreground">
              <div className="p-2 bg-primary/10 rounded-lg">
                <FileText className="w-5 h-5 text-primary" />
              </div>
              <div>
                <span className="text-base font-semibold">
                  {viewTemplate?.name}
                </span>
                <p className="text-xs text-muted-foreground font-normal mt-0.5">{viewTemplate?.description || ''}</p>
              </div>
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            <div className="flex flex-wrap gap-2 mb-4">
              {viewTemplate?.is_system && <Badge variant="outline" className="border-blue-200 bg-blue-50 text-blue-700">系统模板</Badge>}
              {viewTemplate?.is_default && <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">默认</Badge>}
              <Badge variant="outline">{TEMPLATE_TYPES.find(t => t.value === viewTemplate?.template_type)?.label}</Badge>
              {viewTemplate?.is_active ? (
                <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">已启用</Badge>
              ) : (
                <Badge variant="outline">已禁用</Badge>
              )}
            </div>

            <Tabs defaultValue="zh" className="w-full">
              <TabsList className="grid w-full grid-cols-2 bg-muted border border-border p-1 h-auto gap-1 rounded-lg">
                <TabsTrigger value="zh" className="data-[state=active]:bg-background data-[state=active]:text-foreground py-2 text-muted-foreground transition-all rounded-md text-sm">
                  中文提示词
                </TabsTrigger>
                <TabsTrigger value="en" className="data-[state=active]:bg-background data-[state=active]:text-foreground py-2 text-muted-foreground transition-all rounded-md text-sm">
                  英文提示词
                </TabsTrigger>
              </TabsList>
              <TabsContent value="zh" className="mt-4">
                <div className="bg-muted/50 text-foreground p-4 border border-border text-sm whitespace-pre-wrap max-h-[500px] overflow-y-auto rounded-lg">
                  {viewTemplate?.content_zh || '(无中文内容)'}
                </div>
              </TabsContent>
              <TabsContent value="en" className="mt-4">
                <div className="bg-muted/50 text-foreground p-4 border border-border text-sm whitespace-pre-wrap max-h-[500px] overflow-y-auto rounded-lg">
                  {viewTemplate?.content_en || '(No English content)'}
                </div>
              </TabsContent>
            </Tabs>
          </div>
          <DialogFooter className="flex-shrink-0 flex justify-end gap-3 px-6 py-4 border-t border-border">
            <Button variant="outline" onClick={() => copyToClipboard(viewTemplate?.content_zh || viewTemplate?.content_en || '')}>
              <Copy className="w-4 h-4 mr-2" />
              复制内容
            </Button>
            <Button variant="outline" onClick={() => { setShowViewDialog(false); if (viewTemplate) openTestDialog(viewTemplate); }}>
              <Play className="w-4 h-4 mr-2" />
              测试
            </Button>
            {!viewTemplate?.is_system && (
              <Button variant="outline" onClick={() => { setShowViewDialog(false); if (viewTemplate) openEditDialog(viewTemplate); }}>
                <Edit className="w-4 h-4 mr-2" />
                编辑
              </Button>
            )}
            <Button onClick={() => setShowViewDialog(false)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
