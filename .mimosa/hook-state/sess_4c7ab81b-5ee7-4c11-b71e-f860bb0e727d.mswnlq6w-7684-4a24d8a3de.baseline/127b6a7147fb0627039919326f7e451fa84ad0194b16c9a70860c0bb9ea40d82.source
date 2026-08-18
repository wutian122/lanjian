/**
 * Recycle Bin Page
 * Enterprise Blue-White UI
 */

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Link } from "react-router-dom";
import {
  Search,
  GitBranch,
  Calendar,
  Users,
  ExternalLink,
  Trash2,
  RotateCcw,
  AlertTriangle,
  Inbox,
} from "lucide-react";
import { api } from "@/shared/config/database";
import type { Project } from "@/shared/types";
import { toast } from "sonner";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionPanel } from "@/components/ui/section-panel";
import { EmptyState } from "@/components/common/EmptyState";
import { isRepositoryProject, getSourceTypeBadge } from "@/shared/utils/projectUtils";

export default function RecycleBin() {
  const [deletedProjects, setDeletedProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");
  const [showRestoreDialog, setShowRestoreDialog] = useState(false);
  const [showPermanentDeleteDialog, setShowPermanentDeleteDialog] = useState(false);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);

  useEffect(() => {
    loadDeletedProjects();
  }, []);

  const loadDeletedProjects = async () => {
    try {
      setLoading(true);
      const data = await api.getDeletedProjects();
      setDeletedProjects(data);
    } catch (error) {
      console.error('Failed to load deleted projects:', error);
      toast.error("加载已删除项目失败");
    } finally {
      setLoading(false);
    }
  };

  const handleRestoreClick = (project: Project) => {
    setSelectedProject(project);
    setShowRestoreDialog(true);
  };

  const handlePermanentDeleteClick = (project: Project) => {
    setSelectedProject(project);
    setShowPermanentDeleteDialog(true);
  };

  const handleConfirmRestore = async () => {
    if (!selectedProject) return;

    try {
      await api.restoreProject(selectedProject.id);
      toast.success(`项目 "${selectedProject.name}" 已恢复`);
      setShowRestoreDialog(false);
      setSelectedProject(null);
      loadDeletedProjects();
    } catch (error) {
      console.error('Failed to restore project:', error);
      toast.error("恢复项目失败");
    }
  };

  const handleConfirmPermanentDelete = async () => {
    if (!selectedProject) return;

    try {
      await api.permanentlyDeleteProject(selectedProject.id);

      toast.success(`项目 "${selectedProject.name}" 已永久删除`);
      setShowPermanentDeleteDialog(false);
      setSelectedProject(null);
      loadDeletedProjects();
    } catch (error) {
      console.error('Failed to permanently delete project:', error);
      toast.error("永久删除项目失败");
    }
  };

  const filteredProjects = deletedProjects.filter(project =>
    project.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    project.description?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const getRepositoryIcon = (type?: string) => {
    switch (type) {
      case 'github': return '🐙';
      case 'gitlab': return '🦊';
      default: return '📁';
    }
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('zh-CN');
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
        eyebrow="回收站"
        title="回收站"
        description="恢复或永久删除已移除资源。"
      />

      {/* Search Bar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-muted-foreground w-4 h-4" />
          <Input
            placeholder="搜索已删除的项目..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="!pl-10 h-10"
          />
        </div>
        <Badge variant="outline" className="h-9 px-3">
          {deletedProjects.length} 个项目
        </Badge>
      </div>

      {/* Projects Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {filteredProjects.length > 0 ? (
          filteredProjects.map((project) => (
            <SectionPanel key={project.id} className="p-0">
              <div className="p-4 border-b border-border bg-muted/50">
                <div className="flex items-start justify-between">
                  <div className="flex items-center space-x-3">
                    <div className="w-10 h-10 bg-muted border border-border flex items-center justify-center text-lg rounded-lg">
                      {getRepositoryIcon(project.repository_type)}
                    </div>
                    <div>
                      <h3 className="text-base font-semibold text-foreground truncate max-w-[150px] group-hover:text-primary transition-colors">
                        {project.name}
                      </h3>
                      {project.description && (
                        <p className="text-xs text-muted-foreground mt-1 line-clamp-1">
                          {project.description}
                        </p>
                      )}
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    <Badge variant="outline" className="border-red-200 bg-red-50 text-red-700">已删除</Badge>
                    <Badge variant="outline">
                      {getSourceTypeBadge(project.source_type)}
                    </Badge>
                  </div>
                </div>
              </div>

              <div className="p-4 space-y-4">
                <div className="space-y-3">
                  {isRepositoryProject(project) && project.repository_url && (
                    <div className="flex items-center text-xs text-muted-foreground">
                      <GitBranch className="w-4 h-4 mr-2 flex-shrink-0 text-muted-foreground" />
                      <a
                        href={project.repository_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="hover:text-primary transition-colors flex items-center truncate"
                      >
                        <span className="truncate">{project.repository_url.replace('https://', '')}</span>
                        <ExternalLink className="w-3 h-3 ml-1 flex-shrink-0" />
                      </a>
                    </div>
                  )}

                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <div className="flex items-center">
                      <Calendar className="w-4 h-4 mr-2 text-muted-foreground" />
                      删除于 {formatDate(project.updated_at)}
                    </div>
                    <div className="flex items-center">
                      <Users className="w-4 h-4 mr-2 text-muted-foreground" />
                      {project.owner?.full_name || '未知'}
                    </div>
                  </div>
                </div>

                {project.programming_languages && (
                  <div className="flex flex-wrap gap-2">
                    {JSON.parse(project.programming_languages).slice(0, 4).map((lang: string) => (
                      <Badge key={lang} variant="outline" className="text-xs">
                        {lang}
                      </Badge>
                    ))}
                    {JSON.parse(project.programming_languages).length > 4 && (
                      <Badge variant="outline" className="text-xs">
                        +{JSON.parse(project.programming_languages).length - 4}
                      </Badge>
                    )}
                  </div>
                )}

                <div className="flex gap-2 pt-3 border-t border-border">
                  <Button
                    size="sm"
                    variant="outline"
                    className="flex-1 h-9 border-emerald-200 text-emerald-700 hover:bg-emerald-50"
                    onClick={() => handleRestoreClick(project)}
                  >
                    <RotateCcw className="w-4 h-4 mr-2" />
                    恢复
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="flex-1 h-9 border-red-200 text-red-700 hover:bg-red-50"
                    onClick={() => handlePermanentDeleteClick(project)}
                  >
                    <Trash2 className="w-4 h-4 mr-2" />
                    永久删除
                  </Button>
                </div>
              </div>
            </SectionPanel>
          ))
        ) : (
          <div className="col-span-full">
            <SectionPanel>
              <EmptyState
                icon={<Trash2 className="w-12 h-12" />}
                title={searchTerm ? "未找到匹配的项目" : "回收站是空的"}
                description={searchTerm ? "尝试调整搜索条件" : "回收站中没有已删除的项目"}
                action={!searchTerm ? <Button asChild><Link to="/projects">返回项目列表</Link></Button> : undefined}
              />
            </SectionPanel>
          </div>
        )}
      </div>

      {/* Restore Dialog */}
      <AlertDialog open={showRestoreDialog} onOpenChange={setShowRestoreDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="text-lg font-semibold text-foreground flex items-center gap-2">
              <RotateCcw className="w-5 h-5 text-emerald-600" />
              确认恢复项目
            </AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogDescription className="text-muted-foreground">
            您确定要恢复项目 <span className="font-semibold text-foreground">"{selectedProject?.name}"</span> 吗？
            <br /><br />
            恢复后，该项目将重新出现在项目列表中，您可以继续使用该项目的所有功能。
          </AlertDialogDescription>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmRestore}
              className="bg-emerald-600 hover:bg-emerald-700 text-white"
            >
              确认恢复
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Permanent Delete Dialog - Red warning section */}
      <AlertDialog open={showPermanentDeleteDialog} onOpenChange={setShowPermanentDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="text-lg font-semibold text-red-600 flex items-center gap-2">
              <AlertTriangle className="w-5 h-5" />
              警告：永久删除项目
            </AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogDescription className="text-muted-foreground">
            您确定要<span className="font-bold text-red-600">永久删除</span>项目 <span className="font-semibold text-foreground">"{selectedProject?.name}"</span> 吗？
            <br /><br />
            <div className="rounded-xl border border-red-200 bg-red-50 p-5 text-red-900">
              <h3 className="text-base font-semibold">危险操作</h3>
              <p className="text-sm text-red-700 mt-1">此操作无法撤销，请确认后继续。</p>
              <ul className="list-disc list-inside mt-2 text-red-700/80 space-y-1 text-xs">
                <li>项目数据将被永久删除</li>
                <li>相关的审计任务可能会受影响</li>
                <li>无法通过任何方式恢复</li>
              </ul>
            </div>
          </AlertDialogDescription>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmPermanentDelete}
              className="bg-red-600 hover:bg-red-700 text-white"
            >
              确认永久删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
