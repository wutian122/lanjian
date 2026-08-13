/**
 * Audit Rules Management Page
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
import { ScrollArea } from '@/components/ui/scroll-area';
import { toast } from 'sonner';
import { PageHeader } from '@/components/layout/PageHeader';
import { SectionPanel } from '@/components/ui/section-panel';
import { StatusBadge } from '@/components/ui/status-badge';
import { getSeverityText } from '@/shared/utils/uiText';
import {
  Plus,
  Trash2,
  Edit,
  Download,
  Upload,
  Shield,
  Bug,
  Zap,
  Code,
  Settings,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Activity,
  CheckCircle,
} from 'lucide-react';
import {
  getRuleSets,
  createRuleSet,
  updateRuleSet,
  deleteRuleSet,
  exportRuleSet,
  importRuleSet,
  addRuleToSet,
  updateRule,
  deleteRule,
  toggleRule,
  type AuditRuleSet,
  type AuditRule,
  type AuditRuleSetCreate,
  type AuditRuleCreate,
} from '@/shared/api/rules';

const CATEGORIES = [
  { value: 'security', label: '安全', icon: Shield, color: 'text-red-600', bg: 'bg-red-50' },
  { value: 'bug', label: 'Bug', icon: Bug, color: 'text-orange-600', bg: 'bg-orange-50' },
  { value: 'performance', label: '性能', icon: Zap, color: 'text-amber-600', bg: 'bg-amber-50' },
  { value: 'style', label: '代码风格', icon: Code, color: 'text-sky-600', bg: 'bg-sky-50' },
  { value: 'maintainability', label: '可维护性', icon: Settings, color: 'text-violet-600', bg: 'bg-violet-50' },
];

const SEVERITIES = [
  { value: 'critical', label: '严重', color: 'severity-critical' },
  { value: 'high', label: '高', color: 'severity-high' },
  { value: 'medium', label: '中', color: 'severity-medium' },
  { value: 'low', label: '低', color: 'severity-low' },
];

const LANGUAGES = [
  { value: 'all', label: '所有语言' },
  { value: 'python', label: 'Python' },
  { value: 'javascript', label: 'JavaScript' },
  { value: 'typescript', label: 'TypeScript' },
  { value: 'java', label: 'Java' },
  { value: 'go', label: 'Go' },
];

const RULE_TYPES = [
  { value: 'security', label: '安全规则' },
  { value: 'quality', label: '质量规则' },
  { value: 'performance', label: '性能规则' },
  { value: 'custom', label: '自定义规则' },
];

export default function AuditRules() {
  const [ruleSets, setRuleSets] = useState<AuditRuleSet[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedSets, setExpandedSets] = useState<Set<string>>(new Set());
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [showEditDialog, setShowEditDialog] = useState(false);
  const [showRuleDialog, setShowRuleDialog] = useState(false);
  const [showImportDialog, setShowImportDialog] = useState(false);
  const [selectedRuleSet, setSelectedRuleSet] = useState<AuditRuleSet | null>(null);
  const [selectedRule, setSelectedRule] = useState<AuditRule | null>(null);

  const [ruleSetForm, setRuleSetForm] = useState<AuditRuleSetCreate>({
    name: '', description: '', language: 'all', rule_type: 'custom',
  });
  const [ruleForm, setRuleForm] = useState<AuditRuleCreate>({
    rule_code: '', name: '', description: '', category: 'security',
    severity: 'medium', custom_prompt: '', fix_suggestion: '', reference_url: '', enabled: true,
  });
  const [importJson, setImportJson] = useState('');

  useEffect(() => { loadRuleSets(); }, []);

  const loadRuleSets = async () => {
    try {
      setLoading(true);
      const response = await getRuleSets();
      setRuleSets(response.items);
    } catch (error) {
      toast.error('加载规则集失败');
    } finally {
      setLoading(false);
    }
  };

  const toggleExpand = (id: string) => {
    const newExpanded = new Set(expandedSets);
    if (newExpanded.has(id)) newExpanded.delete(id);
    else newExpanded.add(id);
    setExpandedSets(newExpanded);
  };

  const handleCreateRuleSet = async () => {
    try {
      await createRuleSet(ruleSetForm);
      toast.success('规则集已创建');
      setShowCreateDialog(false);
      setRuleSetForm({ name: '', description: '', language: 'all', rule_type: 'custom' });
      loadRuleSets();
    } catch (error) { toast.error('创建失败'); }
  };

  const handleUpdateRuleSet = async () => {
    if (!selectedRuleSet) return;
    try {
      await updateRuleSet(selectedRuleSet.id, ruleSetForm);
      toast.success('更新成功');
      setShowEditDialog(false);
      loadRuleSets();
    } catch (error) { toast.error('更新失败'); }
  };

  const handleDeleteRuleSet = async (id: string) => {
    if (!confirm('确定要删除此规则集吗？')) return;
    try {
      await deleteRuleSet(id);
      toast.success('删除成功');
      loadRuleSets();
    } catch (error: any) { toast.error(error.message || '删除失败'); }
  };

  const handleExport = async (ruleSet: AuditRuleSet) => {
    try {
      const blob = await exportRuleSet(ruleSet.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `${ruleSet.name}.json`; a.click();
      URL.revokeObjectURL(url);
      toast.success('导出成功');
    } catch (error) { toast.error('导出失败'); }
  };

  const handleImport = async () => {
    try {
      const data = JSON.parse(importJson);
      await importRuleSet(data);
      toast.success('导入成功');
      setShowImportDialog(false);
      setImportJson('');
      loadRuleSets();
    } catch (error: any) { toast.error(error.message || '导入失败'); }
  };

  const handleAddRule = async () => {
    if (!selectedRuleSet) return;
    try {
      await addRuleToSet(selectedRuleSet.id, ruleForm);
      toast.success('添加成功');
      setShowRuleDialog(false);
      setRuleForm({ rule_code: '', name: '', description: '', category: 'security', severity: 'medium', custom_prompt: '', fix_suggestion: '', reference_url: '', enabled: true });
      loadRuleSets();
    } catch (error) { toast.error('添加失败'); }
  };

  const handleUpdateRule = async () => {
    if (!selectedRuleSet || !selectedRule) return;
    try {
      await updateRule(selectedRuleSet.id, selectedRule.id, ruleForm);
      toast.success('更新成功');
      setShowRuleDialog(false);
      loadRuleSets();
    } catch (error) { toast.error('更新失败'); }
  };

  const handleDeleteRule = async (ruleSetId: string, ruleId: string) => {
    if (!confirm('确定要删除此规则吗？')) return;
    try {
      await deleteRule(ruleSetId, ruleId);
      toast.success('删除成功');
      loadRuleSets();
    } catch (error) { toast.error('删除失败'); }
  };

  const handleToggleRule = async (ruleSetId: string, ruleId: string) => {
    try {
      const result = await toggleRule(ruleSetId, ruleId);
      toast.success(result.message);
      loadRuleSets();
    } catch (error) { toast.error('操作失败'); }
  };

  const openEditRuleSetDialog = (ruleSet: AuditRuleSet) => {
    setSelectedRuleSet(ruleSet);
    setRuleSetForm({ name: ruleSet.name, description: ruleSet.description || '', language: ruleSet.language, rule_type: ruleSet.rule_type });
    setShowEditDialog(true);
  };

  const openAddRuleDialog = (ruleSet: AuditRuleSet) => {
    setSelectedRuleSet(ruleSet);
    setSelectedRule(null);
    setRuleForm({ rule_code: '', name: '', description: '', category: 'security', severity: 'medium', custom_prompt: '', fix_suggestion: '', reference_url: '', enabled: true });
    setShowRuleDialog(true);
  };

  const openEditRuleDialog = (ruleSet: AuditRuleSet, rule: AuditRule) => {
    setSelectedRuleSet(ruleSet);
    setSelectedRule(rule);
    setRuleForm({ rule_code: rule.rule_code, name: rule.name, description: rule.description || '', category: rule.category, severity: rule.severity, custom_prompt: rule.custom_prompt || '', fix_suggestion: rule.fix_suggestion || '', reference_url: rule.reference_url || '', enabled: rule.enabled });
    setShowRuleDialog(true);
  };

  const getCategoryInfo = (category: string) => CATEGORIES.find(c => c.value === category) || CATEGORIES[0];
  const getSeverityInfo = (severity: string) => SEVERITIES.find(s => s.value === severity) || SEVERITIES[2];

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
        eyebrow="审计规则"
        title="审计规则"
        description="管理安全规则集和启用状态。"
      />

      {/* Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="rounded-xl border border-border bg-card shadow-card p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-muted-foreground">规则集总数</p>
              <p className="text-2xl font-semibold text-foreground">{ruleSets.length}</p>
            </div>
            <div className="text-primary">
              <Shield className="w-6 h-6" />
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card shadow-card p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-muted-foreground">系统规则集</p>
              <p className="text-2xl font-semibold text-sky-600">{ruleSets.filter(r => r.is_system).length}</p>
            </div>
            <div className="text-sky-600">
              <Settings className="w-6 h-6" />
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card shadow-card p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-muted-foreground">总规则数</p>
              <p className="text-2xl font-semibold text-emerald-600">{ruleSets.reduce((acc, r) => acc + r.rules_count, 0)}</p>
            </div>
            <div className="text-emerald-600">
              <CheckCircle className="w-6 h-6" />
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card shadow-card p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-muted-foreground">已启用规则</p>
              <p className="text-2xl font-semibold text-amber-600">{ruleSets.reduce((acc, r) => acc + r.enabled_rules_count, 0)}</p>
            </div>
            <div className="text-amber-600">
              <Activity className="w-6 h-6" />
            </div>
          </div>
        </div>
      </div>

      {/* Action Bar */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-foreground">规则集列表</h2>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setShowImportDialog(true)} size="sm">
            <Upload className="w-4 h-4 mr-2" />
            导入规则集
          </Button>
          <Button onClick={() => setShowCreateDialog(true)} size="sm">
            <Plus className="w-4 h-4 mr-2" />
            新建规则集
          </Button>
        </div>
      </div>

      {/* Rule Sets List */}
      <div className="space-y-4">
        {ruleSets.length === 0 ? (
          <SectionPanel>
            <div className="flex flex-col items-center py-12 text-center">
              <Shield className="w-12 h-12 text-muted-foreground/40 mb-4" />
              <p className="text-base font-medium text-foreground">暂无规则集</p>
              <p className="text-sm text-muted-foreground mt-1">点击"新建规则集"创建自定义审计规则</p>
              <Button className="mt-4" onClick={() => setShowCreateDialog(true)}>
                <Plus className="w-4 h-4 mr-2" />
                创建规则集
              </Button>
            </div>
          </SectionPanel>
        ) : (
          ruleSets.map(ruleSet => (
            <SectionPanel key={ruleSet.id} className={!ruleSet.is_active ? 'opacity-60' : ''}>
              {/* Rule Set Header */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4 cursor-pointer" onClick={() => toggleExpand(ruleSet.id)}>
                  <div className="w-10 h-10 bg-muted border border-border flex items-center justify-center rounded-lg">
                    {expandedSets.has(ruleSet.id) ? <ChevronDown className="w-5 h-5 text-muted-foreground" /> : <ChevronRight className="w-5 h-5 text-muted-foreground" />}
                  </div>
                  <div>
                    <h3 className="font-semibold text-lg text-foreground flex items-center gap-2">
                      {ruleSet.name}
                      {ruleSet.is_system && <Badge variant="outline" className="border-blue-200 bg-blue-50 text-blue-700">系统</Badge>}
                      {ruleSet.is_default && <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">默认</Badge>}
                    </h3>
                    <p className="text-sm text-muted-foreground">{ruleSet.description}</p>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <Badge variant="outline">{LANGUAGES.find(l => l.value === ruleSet.language)?.label}</Badge>
                  <Badge variant="outline">{RULE_TYPES.find(t => t.value === ruleSet.rule_type)?.label}</Badge>
                  <span className="text-sm text-muted-foreground px-3 py-1 bg-muted border border-border rounded-md">
                    {ruleSet.enabled_rules_count}/{ruleSet.rules_count} 启用
                  </span>
                  <Button variant="ghost" size="icon" onClick={() => handleExport(ruleSet)} className="h-9 w-9">
                    <Download className="w-4 h-4" />
                  </Button>
                  {!ruleSet.is_system && (
                    <>
                      <Button variant="ghost" size="icon" onClick={() => openEditRuleSetDialog(ruleSet)} className="h-9 w-9">
                        <Edit className="w-4 h-4" />
                      </Button>
                      <Button variant="ghost" size="icon" onClick={() => handleDeleteRuleSet(ruleSet.id)} className="h-9 w-9 hover:bg-red-50 hover:text-red-600">
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </>
                  )}
                </div>
              </div>

              {/* Rules List */}
              {expandedSets.has(ruleSet.id) && (
                <div className="mt-4 pt-4 border-t border-border">
                  {!ruleSet.is_system && (
                    <Button variant="outline" size="sm" onClick={() => openAddRuleDialog(ruleSet)} className="mb-4">
                      <Plus className="w-4 h-4 mr-2" />
                      添加规则
                    </Button>
                  )}
                  <ScrollArea className="h-[400px]">
                    <div className="space-y-3">
                      {ruleSet.rules.map(rule => {
                        const categoryInfo = getCategoryInfo(rule.category);
                        const CategoryIcon = categoryInfo.icon;
                        return (
                          <div key={rule.id} className={`rounded-lg border border-border bg-muted/30 p-4 hover:border-border/80 transition-all ${!rule.enabled ? 'opacity-50' : ''}`}>
                            <div className="flex items-start justify-between">
                              <div className="flex items-start gap-4">
                                <div className={`w-10 h-10 ${categoryInfo.bg} border border-border flex items-center justify-center rounded-lg`}>
                                  <CategoryIcon className={`w-5 h-5 ${categoryInfo.color}`} />
                                </div>
                                <div>
                                  <div className="flex items-center gap-2 mb-1">
                                    <span className="text-xs bg-muted text-foreground px-2 py-0.5 rounded font-medium">{rule.rule_code}</span>
                                    <span className="font-semibold text-foreground">{rule.name}</span>
                                    <StatusBadge value={rule.severity} label={getSeverityText(rule.severity)} type="severity" />
                                  </div>
                                  {rule.description && <p className="text-sm text-muted-foreground mb-2">{rule.description}</p>}
                                  {rule.reference_url && (
                                    <a href={rule.reference_url} target="_blank" rel="noopener noreferrer" className="text-sm text-primary hover:underline flex items-center gap-1">
                                      参考链接 <ExternalLink className="w-3 h-3" />
                                    </a>
                                  )}
                                </div>
                              </div>
                              <div className="flex items-center gap-2">
                                <Switch checked={rule.enabled} onCheckedChange={() => handleToggleRule(ruleSet.id, rule.id)} />
                                {!ruleSet.is_system && (
                                  <>
                                    <Button variant="ghost" size="icon" onClick={() => openEditRuleDialog(ruleSet, rule)} className="h-8 w-8"><Edit className="w-4 h-4" /></Button>
                                    <Button variant="ghost" size="icon" onClick={() => handleDeleteRule(ruleSet.id, rule.id)} className="h-8 w-8 hover:bg-red-50 hover:text-red-600"><Trash2 className="w-4 h-4" /></Button>
                                  </>
                                )}
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </ScrollArea>
                </div>
              )}
            </SectionPanel>
          ))
        )}
      </div>

      {/* Create Rule Set Dialog */}
      <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
        <DialogContent className="!w-[min(90vw,500px)] !max-w-none max-h-[85vh] flex flex-col p-0 gap-0">
          <DialogHeader className="px-6 py-4 border-b border-border flex-shrink-0">
            <DialogTitle className="flex items-center gap-3 text-foreground">
              <div className="p-2 bg-primary/10 rounded-lg">
                <Shield className="w-5 h-5 text-primary" />
              </div>
              <span className="text-base font-semibold">新建规则集</span>
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            <div className="space-y-2">
              <Label className="text-sm font-medium">名称 *</Label>
              <Input value={ruleSetForm.name} onChange={e => setRuleSetForm({ ...ruleSetForm, name: e.target.value })} placeholder="规则集名称" />
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">描述</Label>
              <Textarea value={ruleSetForm.description} onChange={e => setRuleSetForm({ ...ruleSetForm, description: e.target.value })} placeholder="规则集描述" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label className="text-sm font-medium">适用语言</Label>
                <Select value={ruleSetForm.language} onValueChange={v => setRuleSetForm({ ...ruleSetForm, language: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {LANGUAGES.map(l => <SelectItem key={l.value} value={l.value}>{l.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label className="text-sm font-medium">规则类型</Label>
                <Select value={ruleSetForm.rule_type} onValueChange={v => setRuleSetForm({ ...ruleSetForm, rule_type: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {RULE_TYPES.map(t => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
          <DialogFooter className="flex-shrink-0 flex justify-end gap-3 px-6 py-4 border-t border-border">
            <Button variant="outline" onClick={() => setShowCreateDialog(false)}>取消</Button>
            <Button onClick={handleCreateRuleSet}>创建</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit Rule Set Dialog */}
      <Dialog open={showEditDialog} onOpenChange={setShowEditDialog}>
        <DialogContent className="!w-[min(90vw,500px)] !max-w-none max-h-[85vh] flex flex-col p-0 gap-0">
          <DialogHeader className="px-6 py-4 border-b border-border flex-shrink-0">
            <DialogTitle className="flex items-center gap-3 text-foreground">
              <div className="p-2 bg-primary/10 rounded-lg">
                <Edit className="w-5 h-5 text-primary" />
              </div>
              <span className="text-base font-semibold">编辑规则集</span>
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            <div className="space-y-2">
              <Label className="text-sm font-medium">名称</Label>
              <Input value={ruleSetForm.name} onChange={e => setRuleSetForm({ ...ruleSetForm, name: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">描述</Label>
              <Textarea value={ruleSetForm.description} onChange={e => setRuleSetForm({ ...ruleSetForm, description: e.target.value })} />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label className="text-sm font-medium">适用语言</Label>
                <Select value={ruleSetForm.language} onValueChange={v => setRuleSetForm({ ...ruleSetForm, language: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{LANGUAGES.map(l => <SelectItem key={l.value} value={l.value}>{l.label}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label className="text-sm font-medium">规则类型</Label>
                <Select value={ruleSetForm.rule_type} onValueChange={v => setRuleSetForm({ ...ruleSetForm, rule_type: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{RULE_TYPES.map(t => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
          </div>
          <DialogFooter className="flex-shrink-0 flex justify-end gap-3 px-6 py-4 border-t border-border">
            <Button variant="outline" onClick={() => setShowEditDialog(false)}>取消</Button>
            <Button onClick={handleUpdateRuleSet}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Rule Edit Dialog */}
      <Dialog open={showRuleDialog} onOpenChange={setShowRuleDialog}>
        <DialogContent className="!w-[min(90vw,700px)] !max-w-none max-h-[85vh] flex flex-col p-0 gap-0">
          <DialogHeader className="px-6 py-4 border-b border-border flex-shrink-0">
            <DialogTitle className="flex items-center gap-3 text-foreground">
              <div className="p-2 bg-primary/10 rounded-lg">
                <Code className="w-5 h-5 text-primary" />
              </div>
              <span className="text-base font-semibold">{selectedRule ? '编辑规则' : '添加规则'}</span>
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label className="text-sm font-medium">规则代码 *</Label>
                <Input value={ruleForm.rule_code} onChange={e => setRuleForm({ ...ruleForm, rule_code: e.target.value })} placeholder="如 SEC001" />
              </div>
              <div className="space-y-2">
                <Label className="text-sm font-medium">规则名称 *</Label>
                <Input value={ruleForm.name} onChange={e => setRuleForm({ ...ruleForm, name: e.target.value })} placeholder="规则名称" />
              </div>
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">描述</Label>
              <Textarea value={ruleForm.description} onChange={e => setRuleForm({ ...ruleForm, description: e.target.value })} placeholder="规则描述" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label className="text-sm font-medium">类别</Label>
                <Select value={ruleForm.category} onValueChange={v => setRuleForm({ ...ruleForm, category: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{CATEGORIES.map(c => <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label className="text-sm font-medium">严重程度</Label>
                <Select value={ruleForm.severity} onValueChange={v => setRuleForm({ ...ruleForm, severity: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{SEVERITIES.map(s => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">自定义检测提示词</Label>
              <Textarea value={ruleForm.custom_prompt} onChange={e => setRuleForm({ ...ruleForm, custom_prompt: e.target.value })} placeholder="用于增强LLM检测的自定义提示词" rows={3} />
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">修复建议</Label>
              <Textarea value={ruleForm.fix_suggestion} onChange={e => setRuleForm({ ...ruleForm, fix_suggestion: e.target.value })} placeholder="修复建议模板" rows={2} />
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">参考链接</Label>
              <Input value={ruleForm.reference_url} onChange={e => setRuleForm({ ...ruleForm, reference_url: e.target.value })} placeholder="如 https://owasp.org/..." />
            </div>
          </div>
          <DialogFooter className="flex-shrink-0 flex justify-end gap-3 px-6 py-4 border-t border-border">
            <Button variant="outline" onClick={() => setShowRuleDialog(false)}>取消</Button>
            <Button onClick={selectedRule ? handleUpdateRule : handleAddRule}>{selectedRule ? '保存' : '添加'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Import Dialog */}
      <Dialog open={showImportDialog} onOpenChange={setShowImportDialog}>
        <DialogContent className="!w-[min(90vw,700px)] !max-w-none max-h-[85vh] flex flex-col p-0 gap-0">
          <DialogHeader className="px-6 py-4 border-b border-border flex-shrink-0">
            <DialogTitle className="flex items-center gap-3 text-foreground">
              <div className="p-2 bg-primary/10 rounded-lg">
                <Upload className="w-5 h-5 text-primary" />
              </div>
              <div>
                <span className="text-base font-semibold">导入规则集</span>
                <p className="text-xs text-muted-foreground font-normal mt-0.5">粘贴导出的 JSON 内容</p>
              </div>
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto p-6">
            <Textarea value={importJson} onChange={e => setImportJson(e.target.value)} placeholder='{"name": "...", "rules": [...]}' rows={15} className="text-sm" />
          </div>
          <DialogFooter className="flex-shrink-0 flex justify-end gap-3 px-6 py-4 border-t border-border">
            <Button variant="outline" onClick={() => setShowImportDialog(false)}>取消</Button>
            <Button onClick={handleImport}>导入</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
