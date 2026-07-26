/**
 * Projects Page
 */

import { useState, useEffect, useRef } from "react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Progress } from "@/components/ui/progress";
import {
  Plus,
  Search,
  GitBranch,
  Calendar,
  Users,
  Code,
  Shield,
  Activity,
  Upload,
  FileText,
  AlertCircle,
  Trash2,
  Edit,
  CheckCircle,
  Github,
  Folder,
  ArrowUpRight,
  Key,
  MoreHorizontal
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionPanel } from "@/components/ui/section-panel";
import { MetricCard } from "@/components/ui/metric-card";
import { api } from "@/shared/config/database";
import { validateZipFile } from "@/features/projects/services";
import type { Project, CreateProjectForm } from "@/shared/types";
import { uploadZipFile, getZipFileInfo, type ZipFileMeta } from "@/shared/utils/zipStorage";
import { isRepositoryProject, isZipProject, getSourceTypeBadge } from "@/shared/utils/projectUtils";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import CreateTaskDialog from "@/components/audit/CreateTaskDialog";
import TerminalProgressDialog from "@/components/audit/TerminalProgressDialog";
import { SUPPORTED_LANGUAGES, REPOSITORY_PLATFORMS } from "@/shared/constants";

export default function Projects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [showCreateTaskDialog, setShowCreateTaskDialog] = useState(false);
  const [selectedProjectForTask, setSelectedProjectForTask] = useState<string>("");
  const [showTerminal, setShowTerminal] = useState(false);
  const [currentTaskId, setCurrentTaskId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [projectToDelete, setProjectToDelete] = useState<Project | null>(null);
  const [showEditDialog, setShowEditDialog] = useState(false);
  const [projectToEdit, setProjectToEdit] = useState<Project | null>(null);
  const [editForm, setEditForm] = useState<CreateProjectForm>({
    name: "",
    description: "",
    source_type: "repository",
    repository_url: "",
    repository_type: "github",
    default_branch: "main",
    programming_languages: []
  });
  const [createForm, setCreateForm] = useState<CreateProjectForm>({
    name: "",
    description: "",
    source_type: "repository",
    repository_url: "",
    repository_type: "github",
    default_branch: "main",
    programming_languages: []
  });

  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  // 编辑对话框中的ZIP文件状态
  const [editZipInfo, setEditZipInfo] = useState<ZipFileMeta | null>(null);
  const [editZipFile, setEditZipFile] = useState<File | null>(null);
  const [loadingEditZipInfo, setLoadingEditZipInfo] = useState(false);
  const editZipInputRef = useRef<HTMLInputElement>(null);

  // 将小写语言名转换为显示格式
  const formatLanguageName = (lang: string): string => {
    const nameMap: Record<string, string> = {
      'javascript': 'JavaScript',
      'typescript': 'TypeScript',
      'python': 'Python',
      'java': 'Java',
      'go': 'Go',
      'rust': 'Rust',
      'cpp': 'C++',
      'csharp': 'C#',
      'php': 'PHP',
      'ruby': 'Ruby',
      'swift': 'Swift',
      'kotlin': 'Kotlin'
    };
    return nameMap[lang] || lang.charAt(0).toUpperCase() + lang.slice(1);
  };

  const supportedLanguages = SUPPORTED_LANGUAGES.map(formatLanguageName);

  useEffect(() => {
    loadProjects();
  }, []);

  const loadProjects = async () => {
    try {
      setLoading(true);
      const data = await api.getProjects();
      setProjects(data);
    } catch (error) {
      console.error('Failed to load projects:', error);
      toast.error("加载项目失败");
    } finally {
      setLoading(false);
    }
  };

  const handleFastScanStarted = (taskId: string) => {
    setCurrentTaskId(taskId);
    setShowTerminal(true);
  };

  const handleCreateProject = async () => {
    if (!createForm.name.trim()) {
      toast.error("请输入项目名称");
      return;
    }

    try {
      await api.createProject({
        ...createForm,
      } as any);

      import('@/shared/utils/logger').then(({ logger }) => {
        logger.logUserAction('创建项目', {
          projectName: createForm.name,
          repositoryType: createForm.repository_type,
          languages: createForm.programming_languages,
        });
      });

      toast.success("项目创建成功");
      setShowCreateDialog(false);
      resetCreateForm();
      loadProjects();
    } catch (error) {
      console.error('Failed to create project:', error);
      import('@/shared/utils/errorHandler').then(({ handleError }) => {
        handleError(error, '创建项目失败');
      });
      const errorMessage = error instanceof Error ? error.message : '未知错误';
      toast.error(`创建项目失败: ${errorMessage}`);
    }
  };

  const resetCreateForm = () => {
    setCreateForm({
      name: "",
      description: "",
      source_type: "repository",
      repository_url: "",
      repository_type: "github",
      default_branch: "main",
      programming_languages: []
    });
    setSelectedFile(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const validation = validateZipFile(file);
    if (!validation.valid) {
      toast.error(validation.error);
      return;
    }

    setSelectedFile(file);
    event.target.value = '';
  };

  const handleUploadAndCreate = async () => {
    if (!selectedFile) {
      toast.error("请先选择ZIP文件");
      return;
    }

    if (!createForm.name.trim()) {
      toast.error("请先输入项目名称");
      return;
    }

    try {
      setUploading(true);
      setUploadProgress(0);

      const progressInterval = setInterval(() => {
        setUploadProgress(prev => {
          if (prev >= 100) {
            clearInterval(progressInterval);
            return 100;
          }
          return prev + 20;
        });
      }, 100);

      const project = await api.createProject({
        ...createForm,
        source_type: "zip",
        repository_type: "other",
        repository_url: undefined
      } as any);

      try {
        await uploadZipFile(project.id, selectedFile);
      } catch (error) {
        console.error('保存ZIP文件失败:', error);
      }

      clearInterval(progressInterval);
      setUploadProgress(100);

      import('@/shared/utils/logger').then(({ logger }) => {
        logger.logUserAction('上传ZIP文件创建项目', {
          projectName: project.name,
          fileName: selectedFile.name,
          fileSize: selectedFile.size,
        });
      });

      setShowCreateDialog(false);
      resetCreateForm();
      loadProjects();

      toast.success(`项目 "${project.name}" 已创建`, {
        description: 'ZIP文件已保存，您可以启动代码审计',
        duration: 4000
      });

    } catch (error: any) {
      console.error('Upload failed:', error);
      import('@/shared/utils/errorHandler').then(({ handleError }) => {
        handleError(error, '上传ZIP文件失败');
      });
      const errorMessage = error?.message || '未知错误';
      toast.error(`上传失败: ${errorMessage}`);
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  };

  const filteredProjects = projects.filter(project =>
    project.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    project.description?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const getRepositoryIcon = (type?: string) => {
    switch (type) {
      case 'github': return <Github className="w-5 h-5" />;
      case 'gitlab': return <GitBranch className="w-5 h-5 text-orange-500" />;
      case 'gitea': return <GitBranch className="w-5 h-5 text-green-600" />;
      case 'other': return <Key className="w-5 h-5 text-cyan-500" />;
      default: return <Folder className="w-5 h-5 text-muted-foreground" />;
    }
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('zh-CN');
  };

  const handleCreateTask = (projectId: string) => {
    setSelectedProjectForTask(projectId);
    setShowCreateTaskDialog(true);
  };

  const handleEditClick = async (project: Project) => {
    setProjectToEdit(project);
    setEditForm({
      name: project.name,
      description: project.description || "",
      source_type: project.source_type || "repository",
      repository_url: project.repository_url || "",
      repository_type: project.repository_type || "github",
      default_branch: project.default_branch || "main",
      programming_languages: project.programming_languages ? JSON.parse(project.programming_languages) : []
    });
    setEditZipFile(null);
    setEditZipInfo(null);
    setShowEditDialog(true);

    if (project.source_type === 'zip') {
      setLoadingEditZipInfo(true);
      try {
        const zipInfo = await getZipFileInfo(project.id);
        setEditZipInfo(zipInfo);
      } catch (error) {
        console.error('加载ZIP文件信息失败:', error);
      } finally {
        setLoadingEditZipInfo(false);
      }
    }
  };

  const handleSaveEdit = async () => {
    if (!projectToEdit) return;

    if (!editForm.name.trim()) {
      toast.error("项目名称不能为空");
      return;
    }

    try {
      await api.updateProject(projectToEdit.id, editForm);

      if (editZipFile && editForm.source_type === 'zip') {
        const result = await uploadZipFile(projectToEdit.id, editZipFile);
        if (result.success) {
          toast.success(`ZIP文件已更新: ${result.original_filename}`);
        } else {
          toast.error(`ZIP文件上传失败: ${result.message}`);
        }
      }

      toast.success(`项目 "${editForm.name}" 已更新`);
      setShowEditDialog(false);
      setProjectToEdit(null);
      setEditZipFile(null);
      setEditZipInfo(null);
      loadProjects();
    } catch (error) {
      console.error('Failed to update project:', error);
      toast.error("更新项目失败");
    }
  };

  const handleToggleLanguage = (lang: string) => {
    const currentLanguages = editForm.programming_languages || [];
    const newLanguages = currentLanguages.includes(lang)
      ? currentLanguages.filter(l => l !== lang)
      : [...currentLanguages, lang];

    setEditForm({ ...editForm, programming_languages: newLanguages });
  };

  const handleDeleteClick = (project: Project) => {
    setProjectToDelete(project);
    setShowDeleteDialog(true);
  };

  const handleConfirmDelete = async () => {
    if (!projectToDelete) return;

    try {
      await api.deleteProject(projectToDelete.id);

      import('@/shared/utils/logger').then(({ logger }) => {
        logger.logUserAction('删除项目', {
          projectId: projectToDelete.id,
          projectName: projectToDelete.name,
        });
      });

      toast.success(`项目 "${projectToDelete.name}" 已移到回收站`, {
        description: '您可以在回收站中恢复此项目',
        duration: 4000
      });
      setShowDeleteDialog(false);
      setProjectToDelete(null);
      loadProjects();
    } catch (error) {
      console.error('Failed to delete project:', error);
      import('@/shared/utils/errorHandler').then(({ handleError }) => {
        handleError(error, '删除项目失败');
      });
      const errorMessage = error instanceof Error ? error.message : '未知错误';
      toast.error(`删除项目失败: ${errorMessage}`);
    }
  };

  const handleTaskCreated = () => {
    toast.success("审计任务已创建", {
      description: '因为网络和代码文件大小等因素，审计时长通常至少需要1分钟，请耐心等待...',
      duration: 5000
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="text-center space-y-4">
          <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <p className="text-muted-foreground text-sm">加载项目数据...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6 bg-background min-h-screen">

      {/* 创建项目对话框 */}
      <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
        <DialogTrigger asChild className="hidden">
          <Button>
            <Plus className="w-5 h-5 mr-2" />
            初始化项目
          </Button>
        </DialogTrigger>
        <DialogContent className="!w-[min(90vw,700px)] !max-w-none max-h-[85vh] flex flex-col p-0 gap-0 border border-border rounded-lg">
          <DialogHeader className="px-6 pt-5 flex-shrink-0">
            <DialogTitle className="text-lg font-semibold text-foreground">
              初始化新项目
            </DialogTitle>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto p-6">
            <Tabs defaultValue="repository" className="w-full">
              <TabsList className="flex w-full bg-muted border border-border p-1 h-auto gap-1 rounded-lg">
                <TabsTrigger
                  value="repository"
                  className="flex-1 data-[state=active]:bg-background data-[state=active]:text-foreground font-medium py-2 text-muted-foreground transition-all rounded-md"
                >
                  <GitBranch className="w-4 h-4 mr-2" />
                  Git 仓库
                </TabsTrigger>
                <TabsTrigger
                  value="upload"
                  className="flex-1 data-[state=active]:bg-background data-[state=active]:text-foreground font-medium py-2 text-muted-foreground transition-all rounded-md"
                >
                  <Upload className="w-4 h-4 mr-2" />
                  上传源码
                </TabsTrigger>
              </TabsList>

              <TabsContent value="repository" className="flex flex-col gap-5 mt-5">
                <div className="grid grid-cols-2 gap-5">
                  <div className="space-y-1.5">
                    <Label htmlFor="name" className="text-xs font-medium text-muted-foreground">项目名称 *</Label>
                    <Input
                      id="name"
                      value={createForm.name}
                      onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                      placeholder="输入项目名称"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="repository_type" className="text-xs font-medium text-muted-foreground">认证类型</Label>
                    <Select
                      value={createForm.repository_type}
                      onValueChange={(value: any) => setCreateForm({ ...createForm, repository_type: value })}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {REPOSITORY_PLATFORMS.map((platform) => (
                          <SelectItem key={platform.value} value={platform.value}>
                            {platform.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="description" className="text-xs font-medium text-muted-foreground">描述</Label>
                  <Textarea
                    id="description"
                    value={createForm.description}
                    onChange={(e) => setCreateForm({ ...createForm, description: e.target.value })}
                    placeholder="项目描述..."
                    rows={3}
                    className="min-h-[80px]"
                  />
                </div>

                <div className="grid grid-cols-2 gap-5">
                  <div className="space-y-1.5">
                    <Label htmlFor="repository_url" className="text-xs font-medium text-muted-foreground">仓库地址</Label>
                    <Input
                      id="repository_url"
                      value={createForm.repository_url}
                      onChange={(e) => setCreateForm({ ...createForm, repository_url: e.target.value })}
                      placeholder={
                        createForm.repository_type === 'other'
                          ? "git@github.com:user/repo.git"
                          : "https://github.com/user/repo"
                      }
                    />
                    {createForm.repository_type === 'other' && (
                      <p className="text-xs text-muted-foreground">
                        💡 SSH Key认证请使用 git@ 格式的SSH URL
                      </p>
                    )}
                    {createForm.repository_type !== 'other' && (
                      <p className="text-xs text-muted-foreground">
                        💡 Token认证请使用 https:// 格式的URL
                      </p>
                    )}
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="default_branch" className="text-xs font-medium text-muted-foreground">默认分支</Label>
                    <Input
                      id="default_branch"
                      value={createForm.default_branch}
                      onChange={(e) => setCreateForm({ ...createForm, default_branch: e.target.value })}
                      placeholder="main"
                    />
                  </div>
                </div>

                <div className="space-y-2">
                  <Label className="text-xs font-medium text-muted-foreground">技术栈</Label>
                  <div className="flex flex-wrap gap-2">
                    {supportedLanguages.map((lang) => (
                      <label key={lang} className={`flex items-center space-x-2 px-3 py-1.5 border cursor-pointer transition-all rounded-md ${createForm.programming_languages.includes(lang)
                        ? 'border-primary bg-primary/5 text-primary'
                        : 'border-border hover:border-border text-muted-foreground'
                        }`}>
                        <input
                          type="checkbox"
                          checked={createForm.programming_languages.includes(lang)}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setCreateForm({
                                ...createForm,
                                programming_languages: [...createForm.programming_languages, lang]
                              });
                            } else {
                              setCreateForm({
                                ...createForm,
                                programming_languages: createForm.programming_languages.filter(l => l !== lang)
                              });
                            }
                          }}
                          className="rounded border border-border w-3.5 h-3.5 text-primary focus:ring-0 bg-transparent"
                        />
                        <span className="text-xs font-medium">{lang}</span>
                      </label>
                    ))}
                  </div>
                </div>

                <div className="flex justify-end space-x-4 pt-4 border-t border-border">
                  <Button variant="outline" onClick={() => setShowCreateDialog(false)}>
                    取消
                  </Button>
                  <Button onClick={handleCreateProject}>
                    执行创建
                  </Button>
                </div>
              </TabsContent>

              <TabsContent value="upload" className="flex flex-col gap-5 mt-5">
                <div className="space-y-1.5">
                  <Label htmlFor="upload-name" className="text-xs font-medium text-muted-foreground">项目名称 *</Label>
                  <Input
                    id="upload-name"
                    value={createForm.name}
                    onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                    placeholder="输入项目名称"
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="upload-description" className="text-xs font-medium text-muted-foreground">描述</Label>
                  <Textarea
                    id="upload-description"
                    value={createForm.description}
                    onChange={(e) => setCreateForm({ ...createForm, description: e.target.value })}
                    placeholder="项目描述..."
                    rows={3}
                    className="min-h-[80px]"
                  />
                </div>

                <div className="space-y-2">
                  <Label className="text-xs font-medium text-muted-foreground">技术栈</Label>
                  <div className="flex flex-wrap gap-2">
                    {supportedLanguages.map((lang) => (
                      <label key={lang} className={`flex items-center space-x-2 px-3 py-1.5 border cursor-pointer transition-all rounded-md ${createForm.programming_languages.includes(lang)
                        ? 'border-primary bg-primary/5 text-primary'
                        : 'border-border hover:border-border text-muted-foreground'
                        }`}>
                        <input
                          type="checkbox"
                          checked={createForm.programming_languages.includes(lang)}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setCreateForm({
                                ...createForm,
                                programming_languages: [...createForm.programming_languages, lang]
                              });
                            } else {
                              setCreateForm({
                                ...createForm,
                                programming_languages: createForm.programming_languages.filter(l => l !== lang)
                              });
                            }
                          }}
                          className="rounded border border-border w-3.5 h-3.5 text-primary focus:ring-0 bg-transparent"
                        />
                        <span className="text-xs font-medium">{lang}</span>
                      </label>
                    ))}
                  </div>
                </div>

                <div className="space-y-4">
                  <Label className="text-xs font-medium text-muted-foreground">源代码</Label>

                  {!selectedFile ? (
                    <div
                      className="border border-dashed border-border bg-muted/30 rounded-lg p-6 text-center hover:bg-muted hover:border-border transition-colors cursor-pointer group"
                      onClick={() => fileInputRef.current?.click()}
                    >
                      <Upload className="w-10 h-10 text-muted-foreground mx-auto mb-3 group-hover:text-primary transition-colors" />
                      <h3 className="text-base font-semibold text-foreground mb-1">上传 ZIP 归档</h3>
                      <p className="text-xs text-muted-foreground mb-3">
                        最大: 500MB · 格式: .ZIP
                      </p>
                      <input
                        ref={fileInputRef}
                        type="file"
                        accept=".zip"
                        onChange={handleFileSelect}
                        className="hidden"
                        disabled={uploading}
                      />
                      <Button
                        type="button"
                        variant="outline"
                        className="h-8 text-xs"
                        disabled={uploading || !createForm.name.trim()}
                        onClick={(e) => {
                          e.stopPropagation();
                          fileInputRef.current?.click();
                        }}
                      >
                        <FileText className="w-3 h-3 mr-2" />
                        选择文件
                      </Button>
                    </div>
                  ) : (
                    <div className="border border-border bg-muted/30 p-4 flex items-center justify-between rounded-lg">
                      <div className="flex items-center space-x-3 overflow-hidden">
                        <div className="w-10 h-10 bg-muted border border-border rounded-lg flex items-center justify-center flex-shrink-0">
                          <FileText className="w-5 h-5 text-primary" />
                        </div>
                        <div className="min-w-0">
                          <p className="font-medium text-sm text-foreground truncate">{selectedFile.name}</p>
                          <p className="text-xs text-muted-foreground">{(selectedFile.size / 1024 / 1024).toFixed(2)} MB</p>
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => setSelectedFile(null)}
                        disabled={uploading}
                        className="hover:bg-red-50 hover:text-red-600"
                      >
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  )}

                  {uploading && (
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>上传并分析中...</span>
                        <span className="text-primary">{uploadProgress}%</span>
                      </div>
                      <Progress value={uploadProgress} className="h-2 bg-muted [&>div]:bg-primary" />
                    </div>
                  )}

                  <div className="bg-amber-50 border border-amber-200 p-3 rounded-lg">
                    <div className="flex items-start space-x-3">
                      <AlertCircle className="w-4 h-4 text-amber-600 mt-0.5" />
                      <div className="text-xs text-amber-800">
                        <p className="font-medium mb-1">上传协议:</p>
                        <ul className="space-y-0.5 list-disc list-inside">
                          <li>确保完整的项目代码</li>
                          <li>移除 node_modules 等依赖目录</li>
                          <li>包含必要的配置文件</li>
                        </ul>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="flex justify-end space-x-4 pt-4 border-t border-border mt-auto">
                  <Button variant="outline" onClick={() => setShowCreateDialog(false)} disabled={uploading}>
                    取消
                  </Button>
                  <Button
                    onClick={handleUploadAndCreate}
                    disabled={!selectedFile || uploading}
                  >
                    {uploading ? '上传中...' : '执行创建'}
                  </Button>
                </div>
              </TabsContent>
            </Tabs>
          </div>
        </DialogContent>
      </Dialog>

      <PageHeader
        eyebrow="项目管理"
        title="项目"
        actions={
          <Button onClick={() => setShowCreateDialog(true)}>
            <Plus className="w-4 h-4 mr-2" />
            新建项目
          </Button>
        }
      />

      {/* Stats Section */}
      {projects.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <MetricCard label="项目总数" value={projects.length} icon={<Code className="w-5 h-5" />} tone="blue" />
          <MetricCard label="活跃项目" value={projects.filter(p => p.is_active).length} icon={<Activity className="w-5 h-5" />} tone="green" />
          <MetricCard label="远程仓库" value={projects.filter(p => isRepositoryProject(p)).length} icon={<GitBranch className="w-5 h-5" />} tone="blue" />
          <MetricCard label="ZIP上传" value={projects.filter(p => isZipProject(p)).length} icon={<Upload className="w-5 h-5" />} tone="amber" />
        </div>
      )}

      {/* Search and Filter */}
      <div className="rounded-xl border border-border bg-card p-4 flex items-center gap-4 shadow-card">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-muted-foreground w-4 h-4 z-10" />
          <Input
            placeholder="搜索项目..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="!pl-10"
          />
        </div>
        <Button onClick={() => setShowCreateDialog(true)}>
          <Plus className="w-4 h-4 mr-2" />
          新建项目
        </Button>
      </div>

      {/* Project List */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {filteredProjects.length > 0 ? (
          filteredProjects.map((project) => (
              <div key={project.id} className="rounded-xl border border-border bg-card shadow-card flex flex-col h-full group">
                {/* Card Header */}
              <div className="p-4 border-b border-border bg-muted/30 flex justify-between items-start rounded-t-xl opacity-100">
                <div className="flex items-center space-x-3">
                  <div className="w-10 h-10 border border-border bg-muted rounded-lg flex items-center justify-center text-muted-foreground">
                    {getRepositoryIcon(project.repository_type)}
                  </div>
                  <div>
                    <h3 className="lanjian-project-title font-semibold text-base transition-colors">
                      <Link
                        to={`/projects/${project.id}`}
                        className="hover:underline"
                      >
                        {project.name}
                      </Link>
                    </h3>
                    <div className="flex items-center mt-1 space-x-2">
                      <Badge variant="outline" className={project.is_active ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-slate-200 bg-slate-50 text-slate-600'}>
                        {project.is_active ? '活跃' : '暂停'}
                      </Badge>
                      <Badge variant="outline" className={isRepositoryProject(project) ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-amber-200 bg-amber-50 text-amber-700'}>
                        {getSourceTypeBadge(project.source_type)}
                      </Badge>
                    </div>
                  </div>
                </div>
              </div>

              {/* Card Body */}
              <div className="p-4 flex-1 space-y-3">
                {project.description && (
                  <p className="text-sm text-muted-foreground line-clamp-2 border-l-2 border-border pl-2">
                    {project.description}
                  </p>
                )}

                <div className="space-y-2">
                  {project.repository_url && (
                    <div className="flex items-center text-xs text-muted-foreground bg-muted p-2 border border-border rounded">
                      <GitBranch className="w-3 h-3 mr-2 flex-shrink-0 text-muted-foreground" />
                      <a
                        href={project.repository_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="hover:text-primary transition-colors truncate"
                      >
                        {project.repository_url.replace('https://', '')}
                      </a>
                    </div>
                  )}

                  <div className="flex justify-between items-center text-xs text-muted-foreground">
                    <span className="flex items-center"><Calendar className="w-3 h-3 mr-1" /> {formatDate(project.created_at)}</span>
                    <span className="flex items-center"><Users className="w-3 h-3 mr-1" /> {project.owner?.full_name || '未知'}</span>
                  </div>
                </div>

                {project.programming_languages && (
                  <div className="flex flex-wrap gap-1">
                    {JSON.parse(project.programming_languages).slice(0, 4).map((lang: string) => (
                      <span key={lang} className="text-xs font-medium border border-blue-200 px-1.5 py-0.5 bg-blue-50 text-blue-700 rounded">
                        {lang.toUpperCase()}
                      </span>
                    ))}
                    {JSON.parse(project.programming_languages).length > 4 && (
                      <span className="text-xs font-medium border border-border px-1.5 py-0.5 bg-muted text-muted-foreground rounded">
                        +{JSON.parse(project.programming_languages).length - 4}
                      </span>
                    )}
                  </div>
                )}
              </div>

              {/* Card Footer */}
              <div className="p-4 border-t border-border bg-muted/30 flex items-center gap-2 rounded-b-xl">
                <Link to={`/projects/${project.id}`}>
                  <Button variant="outline" size="sm" className="h-9 text-xs">
                    <Code className="w-3 h-3 mr-1.5" />
                    查看详情
                  </Button>
                </Link>
                <Button size="sm" className="h-9 text-xs" onClick={() => handleCreateTask(project.id)}>
                  <Shield className="w-3 h-3 mr-1.5" />
                  审计
                </Button>
                <div className="ml-auto">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button size="sm" variant="ghost" className="h-9 w-9 p-0">
                        <MoreHorizontal className="w-4 h-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onClick={() => handleEditClick(project)}>
                        <Edit className="w-3.5 h-3.5 mr-2" />
                        编辑
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleDeleteClick(project)} className="text-red-500">
                        <Trash2 className="w-3.5 h-3.5 mr-2" />
                        删除
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </div>
            </div>
          ))
        ) : (
          <div className="col-span-full">
            <div className="rounded-xl border border-dashed border-border bg-card p-16 text-center shadow-card">
              <Code className="w-16 h-16 text-muted-foreground mx-auto mb-4" />
              <h3 className="text-xl font-semibold text-foreground mb-2">
                {searchTerm ? '未找到匹配项' : '未初始化项目'}
              </h3>
                <p className="text-muted-foreground mb-6">
                {searchTerm ? '调整搜索参数' : '初始化第一个项目以开始'}
              </p>
              {!searchTerm && (
                <Button onClick={() => setShowCreateDialog(true)}>
                  <Plus className="w-4 h-4 mr-2" />
                  初始化项目
                </Button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Create Task Dialog */}
      <CreateTaskDialog
        open={showCreateTaskDialog}
        onOpenChange={setShowCreateTaskDialog}
        onTaskCreated={handleTaskCreated}
        onFastScanStarted={handleFastScanStarted}
        preselectedProjectId={selectedProjectForTask}
      />

      {/* Terminal Progress Dialog for Fast Scan */}
      <TerminalProgressDialog
        open={showTerminal}
        onOpenChange={setShowTerminal}
        taskId={currentTaskId}
        taskType="repository"
      />

      {/* Edit Dialog */}
      <Dialog open={showEditDialog} onOpenChange={setShowEditDialog}>
        <DialogContent className="!w-[min(90vw,700px)] !max-w-none max-h-[85vh] flex flex-col p-0 gap-0 border border-border rounded-lg">
          <DialogHeader className="px-6 pt-5 flex-shrink-0">
            <DialogTitle className="text-lg font-semibold flex items-center gap-2 text-foreground">
              编辑项目配置
              {projectToEdit && (
                <Badge variant="outline" className={editForm.source_type === 'repository' ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-amber-200 bg-amber-50 text-amber-700'}>
                  {editForm.source_type === 'repository' ? '远程仓库' : 'ZIP上传'}
                </Badge>
              )}
            </DialogTitle>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto p-6 space-y-6">
            {/* 基本信息 */}
            <div className="space-y-4">
              <h3 className="text-sm font-medium text-muted-foreground border-b border-border pb-2">基本信息</h3>
              <div>
                <Label htmlFor="edit-name" className="text-xs font-medium text-muted-foreground">项目名称 *</Label>
                <Input
                  id="edit-name"
                  value={editForm.name}
                  onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                  className="mt-1"
                />
              </div>
              <div>
                <Label htmlFor="edit-description" className="text-xs font-medium text-muted-foreground">描述</Label>
                <Textarea
                  id="edit-description"
                  value={editForm.description}
                  onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
                  rows={3}
                  className="mt-1"
                />
              </div>
            </div>

            {/* 仓库信息 - 仅远程仓库类型显示 */}
            {editForm.source_type === 'repository' && (
              <div className="space-y-4">
                <h3 className="text-sm font-medium text-muted-foreground border-b border-border pb-2 flex items-center gap-2">
                  <GitBranch className="w-4 h-4" />
                  仓库信息
                </h3>

                <div>
                  <Label htmlFor="edit-repo-url" className="text-xs font-medium text-muted-foreground">仓库地址</Label>
                  <Input
                    id="edit-repo-url"
                    value={editForm.repository_url}
                    onChange={(e) => setEditForm({ ...editForm, repository_url: e.target.value })}
                    placeholder={
                      editForm.repository_type === 'other'
                        ? "git@github.com:user/repo.git"
                        : "https://github.com/user/repo"
                    }
                    className="mt-1"
                  />
                  {editForm.repository_type === 'other' && (
                    <p className="text-xs text-muted-foreground mt-1">
                      💡 SSH Key认证请使用 git@ 格式的SSH URL
                    </p>
                  )}
                  {editForm.repository_type !== 'other' && (
                    <p className="text-xs text-muted-foreground mt-1">
                      💡 Token认证请使用 https:// 格式的URL
                    </p>
                  )}
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <Label htmlFor="edit-repo-type" className="text-xs font-medium text-muted-foreground">认证类型</Label>
                    <Select
                      value={editForm.repository_type}
                      onValueChange={(value: any) => setEditForm({ ...editForm, repository_type: value })}
                    >
                      <SelectTrigger id="edit-repo-type" className="mt-1">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {REPOSITORY_PLATFORMS.map((platform) => (
                          <SelectItem key={platform.value} value={platform.value}>
                            {platform.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div>
                    <Label htmlFor="edit-default-branch" className="text-xs font-medium text-muted-foreground">默认分支</Label>
                    <Input
                      id="edit-default-branch"
                      value={editForm.default_branch}
                      onChange={(e) => setEditForm({ ...editForm, default_branch: e.target.value })}
                      placeholder="main"
                      className="mt-1"
                    />
                  </div>
                </div>
              </div>
            )}

            {/* ZIP项目文件管理 */}
            {editForm.source_type === 'zip' && (
              <div className="space-y-4">
                <h3 className="text-sm font-medium text-muted-foreground border-b border-border pb-2 flex items-center gap-2">
                  <Upload className="w-4 h-4" />
                  ZIP文件管理
                </h3>

                {loadingEditZipInfo ? (
                  <div className="flex items-center space-x-3 p-4 bg-blue-50 border border-blue-200 rounded-lg">
                    <div className="h-5 w-5 animate-spin rounded-full border-2 border-blue-600 border-t-transparent"></div>
                    <p className="text-sm text-blue-700 font-medium">正在加载ZIP文件信息...</p>
                  </div>
                ) : editZipInfo?.has_file ? (
                  <div className="bg-emerald-50 border border-emerald-200 p-4 rounded-lg">
                    <div className="flex items-start space-x-3">
                      <FileText className="w-5 h-5 text-emerald-600 mt-0.5" />
                      <div className="flex-1">
                        <p className="font-medium text-emerald-800 mb-1">当前存储的ZIP文件</p>
                        <p className="text-emerald-700 text-sm">
                          文件名: {editZipInfo.original_filename}
                          {editZipInfo.file_size && (
                            <> ({editZipInfo.file_size >= 1024 * 1024
                              ? `${(editZipInfo.file_size / 1024 / 1024).toFixed(2)} MB`
                              : `${(editZipInfo.file_size / 1024).toFixed(2)} KB`
                            })</>
                          )}
                        </p>
                        {editZipInfo.uploaded_at && (
                          <p className="text-emerald-600 text-xs mt-0.5">
                            上传时间: {new Date(editZipInfo.uploaded_at).toLocaleString('zh-CN')}
                          </p>
                        )}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="bg-amber-50 border border-amber-200 p-4 rounded-lg">
                    <div className="flex items-start space-x-3">
                      <AlertCircle className="w-5 h-5 text-amber-600 mt-0.5" />
                      <div>
                        <p className="font-medium text-amber-800 mb-1">暂无ZIP文件</p>
                        <p className="text-amber-700 text-sm">
                          此项目还没有上传ZIP文件，请上传文件以便进行代码审计。
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                {/* 上传新文件 */}
                <div className="space-y-2">
                  <Label className="text-xs font-medium text-muted-foreground">
                    {editZipInfo?.has_file ? '更新ZIP文件' : '上传ZIP文件'}
                  </Label>
                  <input
                    ref={editZipInputRef}
                    type="file"
                    accept=".zip"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) {
                        const validation = validateZipFile(file);
                        if (!validation.valid) {
                          toast.error(validation.error || "文件无效");
                          e.target.value = '';
                          return;
                        }
                        setEditZipFile(file);
                        toast.success(`已选择文件: ${file.name}`);
                      }
                    }}
                  />

                  {editZipFile ? (
                    <div className="flex items-center justify-between p-3 bg-blue-50 border border-blue-200 rounded-lg">
                      <div className="flex items-center space-x-2">
                        <FileText className="w-4 h-4 text-blue-600" />
                        <span className="text-sm font-medium text-blue-700">{editZipFile.name}</span>
                        <span className="text-xs text-muted-foreground">
                          ({(editZipFile.size / 1024 / 1024).toFixed(2)} MB)
                        </span>
                      </div>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setEditZipFile(null)}
                        className="h-7 text-xs"
                      >
                        取消
                      </Button>
                    </div>
                  ) : (
                    <Button
                      variant="outline"
                      onClick={() => editZipInputRef.current?.click()}
                      className="w-full"
                    >
                      <Upload className="w-4 h-4 mr-2" />
                      {editZipInfo?.has_file ? '选择新文件替换' : '选择ZIP文件'}
                    </Button>
                  )}
                </div>
              </div>
            )}

            {/* 技术栈 */}
            <div className="space-y-4">
              <h3 className="text-sm font-medium text-muted-foreground border-b border-border pb-2">技术栈</h3>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                {supportedLanguages.map((lang) => (
                  <div
                    key={lang}
                    className={`flex items-center space-x-2 p-2 border cursor-pointer transition-all rounded-md ${editForm.programming_languages?.includes(lang)
                      ? 'border-primary bg-primary/5 text-primary'
                      : 'border-border hover:border-border text-muted-foreground'
                      }`}
                    onClick={() => handleToggleLanguage(lang)}
                  >
                    <div
                      className={`w-4 h-4 border-2 rounded flex items-center justify-center ${editForm.programming_languages?.includes(lang)
                        ? 'bg-primary border-primary'
                        : 'border-border'
                        }`}
                    >
                      {editForm.programming_languages?.includes(lang) && (
                        <CheckCircle className="w-3 h-3 text-primary-foreground" />
                      )}
                    </div>
                    <span className="text-sm font-medium">{lang}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="flex-shrink-0 flex justify-end gap-3 px-6 py-4 bg-muted/30 border-t border-border">
            <Button variant="outline" onClick={() => setShowEditDialog(false)}>
              取消
            </Button>
            <Button onClick={handleSaveEdit}>
              保存更改
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete Dialog */}
      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent className="border border-border p-0">
          <AlertDialogHeader className="p-6">
            <AlertDialogTitle className="text-lg font-semibold flex items-center gap-2 text-foreground">
              <Trash2 className="w-5 h-5 text-red-500" />
              确认删除
            </AlertDialogTitle>
            <AlertDialogDescription className="text-muted-foreground">
              您确定要移动 <span className="font-semibold text-red-500">"{projectToDelete?.name}"</span> 到回收站吗？
            </AlertDialogDescription>
          </AlertDialogHeader>

          <div className="px-6 pb-6">
            <div className="bg-blue-50 border border-blue-200 p-4 rounded-lg">
              <p className="text-blue-700 font-medium mb-2 text-sm">系统通知:</p>
              <ul className="list-none text-blue-600 space-y-1 text-sm">
                <li className="flex items-center gap-2"><span className="text-blue-400">›</span> 项目移至回收站</li>
                <li className="flex items-center gap-2"><span className="text-blue-400">›</span> 可恢复</li>
                <li className="flex items-center gap-2"><span className="text-blue-400">›</span> 审计数据保留</li>
                <li className="flex items-center gap-2"><span className="text-blue-400">›</span> 在回收站中永久删除</li>
              </ul>
            </div>
          </div>

          <AlertDialogFooter className="p-4 border-t border-border bg-muted/30">
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmDelete}
              className="bg-red-500 hover:bg-red-600"
            >
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
