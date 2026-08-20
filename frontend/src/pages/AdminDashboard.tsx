/**
 * Admin Dashboard Page
 * Enterprise Blue-White UI
 */

import { useState, useEffect } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DatabaseManager } from "@/components/database/DatabaseManager";
import { SystemConfig } from "@/components/system/SystemConfig";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import { apiClient } from "@/shared/api/serverClient";
import { useAuth } from "@/shared/context/AuthContext";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  Settings, Database, Users, Search, Shield, UserX,
  UserCheck, RefreshCw, UserPlus, Building,
  Phone, Eye, EyeOff, Trash2,
} from "lucide-react";

interface UserRecord {
  id: string;
  username: string;
  email: string;
  full_name: string;
  department: string;
  phone: string;
  role: string;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string;
}

export default function AdminDashboard() {
  const { user } = useAuth();
  const [users, setUsers] = useState<UserRecord[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  // 创建用户弹窗状态
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [creating, setCreating] = useState(false);
  const [showPwd, setShowPwd] = useState(false);
  const [form, setForm] = useState({
    username: "", password: "", confirm_password: "",
    full_name: "", department: "", phone: "", role: "user",
  });

  const isSuperAdmin = user?.role === "super_admin";
  // C3: 后端 RBAC 允许 admin 管理下辖用户（parent_admin_id 数据范围），前端同步放开用户管理入口
  const canManageUsers = isSuperAdmin || user?.role === "admin";

  const loadUsers = async () => {
    try {
      setLoadingUsers(true);
      const res = await apiClient.get("/users/");
      const data = res.data;
      setUsers(Array.isArray(data.users) ? data.users : Array.isArray(data) ? data : []);
    } catch (err) {
      toast.error("加载用户列表失败");
    } finally {
      setLoadingUsers(false);
    }
  };

  useEffect(() => {
    if (canManageUsers) loadUsers();
  }, []);

  const handleToggleStatus = async (userId: string) => {
    try {
      await apiClient.post(`/users/${userId}/toggle-status`);
      toast.success("操作成功");
      loadUsers();
    } catch (err) {
      toast.error("操作失败");
    }
  };

  const handleDelete = async (userId: string, username: string) => {
    if (!window.confirm(`确定要删除用户 "${username}" 吗？此操作不可恢复。`)) return;
    try {
      await apiClient.delete(`/users/${userId}`);
      toast.success("用户已删除");
      loadUsers();
    } catch (err) {
      toast.error("删除失败");
    }
  };

  const resetForm = () => {
    setForm({ username: "", password: "", confirm_password: "", full_name: "", department: "", phone: "", role: "user" });
    setShowPwd(false);
  };

  const handleCreate = async () => {
    if (!form.username || !form.password || !form.confirm_password || !form.full_name || !form.department || !form.phone) {
      toast.error("请填写所有必填字段");
      return;
    }
    if (form.password !== form.confirm_password) {
      toast.error("两次输入的密码不一致");
      return;
    }
    try {
      setCreating(true);
      await apiClient.post("/users/", {
        username: form.username,
        password: form.password,
        confirm_password: form.confirm_password,
        full_name: form.full_name,
        department: form.department,
        phone: form.phone,
        role: form.role,
      });
      toast.success("用户创建成功");
      setShowCreateDialog(false);
      resetForm();
      loadUsers();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "创建失败");
    } finally {
      setCreating(false);
    }
  };

  const filteredUsers = users.filter((u) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      (u.email || "").toLowerCase().includes(q) ||
      (u.username || "").toLowerCase().includes(q) ||
      (u.full_name || "").toLowerCase().includes(q) ||
      (u.department || "").toLowerCase().includes(q)
    );
  });

  const roleLabel = (role: string) => {
    const map: Record<string, { label: string; color: string }> = {
      super_admin: { label: "超级管理员", color: "bg-rose-500/20 text-rose-400 border-rose-500/30" },
      admin: { label: "管理员", color: "bg-amber-500/20 text-amber-400 border-amber-500/30" },
      user: { label: "普通用户", color: "bg-sky-500/20 text-sky-400 border-sky-500/30" },
      member: { label: "普通用户", color: "bg-sky-500/20 text-sky-400 border-sky-500/30" },
    };
    return map[role] || { label: role, color: "bg-muted text-muted-foreground border-border" };
  };

  return (
    <div className="space-y-6 p-6 min-h-screen">
      <PageHeader
        eyebrow="系统配置"
        title="系统管理"
        description="配置模型、数据库和平台运行参数。"
      />

      <Tabs defaultValue="config" className="w-full">
        <TabsList className="grid w-full grid-cols-3 bg-muted border border-border p-1 h-auto gap-1 rounded-lg mb-6">
          <TabsTrigger value="config" className="data-[state=active]:bg-background data-[state=active]:text-foreground py-2.5 text-muted-foreground transition-all rounded-md text-sm flex items-center gap-2">
            <Settings className="w-4 h-4" /> 系统配置
          </TabsTrigger>
          <TabsTrigger value="data" className="data-[state=active]:bg-background data-[state=active]:text-foreground py-2.5 text-muted-foreground transition-all rounded-md text-sm flex items-center gap-2">
            <Database className="w-4 h-4" /> 数据管理
          </TabsTrigger>
          <TabsTrigger value="users" className="data-[state=active]:bg-background data-[state=active]:text-foreground py-2.5 text-muted-foreground transition-all rounded-md text-sm flex items-center gap-2">
            <Users className="w-4 h-4" /> 用户管理
          </TabsTrigger>
        </TabsList>

        <TabsContent value="config" className="flex flex-col gap-6">
          <SystemConfig />
        </TabsContent>

        <TabsContent value="data" className="space-y-6">
          <DatabaseManager />
        </TabsContent>

        <TabsContent value="users" className="space-y-6">
          {!canManageUsers ? (
            <div className="rounded-xl border border-border bg-card shadow-card p-8 text-center">
              <Shield className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
              <h2 className="text-lg font-semibold text-foreground mb-2">权限不足</h2>
              <p className="text-muted-foreground text-sm">仅管理员及以上角色可访问用户管理功能</p>
            </div>
          ) : (
            <div className="rounded-xl border border-border bg-card shadow-card p-6">
              <div className="flex items-center justify-between mb-6">
                <div className="flex items-center gap-3">
                  <Users className="w-5 h-5 text-primary" />
                  <h2 className="text-lg font-semibold text-foreground">用户管理</h2>
                  <Badge variant="outline">
                    {users.length} 个用户
                  </Badge>
                </div>
                <div className="flex items-center gap-3">
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                    <Input
                      placeholder="搜索用户名、姓名或部门..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      className="pl-10 h-10 w-64"
                    />
                  </div>
                  <Button onClick={loadUsers} variant="outline" size="sm" className="h-10">
                    <RefreshCw className="w-4 h-4 mr-2" /> 刷新
                  </Button>
                  <Button onClick={() => setShowCreateDialog(true)} size="sm" className="h-10">
                    <UserPlus className="w-4 h-4 mr-2" /> 创建用户
                  </Button>
                </div>
              </div>

              {loadingUsers ? (
                <div className="flex items-center justify-center py-12">
                  <div className="loading-spinner" />
                </div>
              ) : filteredUsers.length === 0 ? (
                <div className="text-center py-12">
                  <Shield className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
                  <p className="text-muted-foreground text-sm">
                    {searchQuery ? "未找到匹配的用户" : "暂无用户"}
                  </p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border">
                        <th className="text-left p-3 text-muted-foreground font-medium">用户名</th>
                        <th className="text-left p-3 text-muted-foreground font-medium">姓名</th>
                        <th className="text-left p-3 text-muted-foreground font-medium">部门</th>
                        <th className="text-left p-3 text-muted-foreground font-medium">电话</th>
                        <th className="text-left p-3 text-muted-foreground font-medium">角色</th>
                        <th className="text-left p-3 text-muted-foreground font-medium">状态</th>
                        <th className="text-left p-3 text-muted-foreground font-medium">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredUsers.map((u) => {
                        const r = roleLabel(u.role);
                        return (
                          <tr key={u.id} className="border-b border-border">
                            <td className="p-3">
                              <p className="text-foreground font-medium text-sm">{u.username || "-"}</p>
                              <p className="text-xs text-muted-foreground">{u.email || ""}</p>
                            </td>
                            <td className="p-3"><p className="text-foreground text-sm">{u.full_name || "-"}</p></td>
                            <td className="p-3"><p className="text-muted-foreground text-sm">{u.department || "-"}</p></td>
                            <td className="p-3"><p className="text-muted-foreground text-sm">{u.phone || "-"}</p></td>
                            <td className="p-3">
                              <Badge variant="outline" className={`text-xs ${r.color}`}>{r.label}</Badge>
                            </td>
                            <td className="p-3">
                              <Badge variant="outline" className={u.is_active
                                ? "border-emerald-200 bg-emerald-50 text-emerald-700 text-xs"
                                : "border-red-200 bg-red-50 text-red-700 text-xs"
                              }>
                                {u.is_active ? "启用" : "禁用"}
                              </Badge>
                            </td>
                            <td className="p-3">
                              <div className="flex items-center gap-1">
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => handleToggleStatus(u.id)}
                                  className={u.is_active
                                    ? "text-red-600 hover:text-red-700 hover:bg-red-50"
                                    : "text-emerald-600 hover:text-emerald-700 hover:bg-emerald-50"
                                  }
                                >
                                  {u.is_active ? (
                                    <><UserX className="w-3 h-3 mr-1" /> 禁用</>
                                  ) : (
                                    <><UserCheck className="w-3 h-3 mr-1" /> 启用</>
                                  )}
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => handleDelete(u.id, u.username || u.email)}
                                  className="text-red-600 hover:text-red-700 hover:bg-red-50"
                                >
                                  <Trash2 className="w-3 h-3 mr-1" /> 删除
                                </Button>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* 创建用户弹窗 */}
      <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-lg font-semibold text-foreground flex items-center gap-2">
              <UserPlus className="w-5 h-5 text-primary" />
              创建新用户
            </DialogTitle>
          </DialogHeader>
          <div className="overflow-y-auto max-h-[55vh] px-1 space-y-4">
            <div className="space-y-2">
              <Label className="text-sm font-medium">
                用户名 <span className="text-red-500">*</span>
              </Label>
              <Input value={form.username} onChange={(e) => setForm({...form, username: e.target.value})}
                placeholder="用于登录的用户名" className="h-10" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label className="text-sm font-medium">
                  密码 <span className="text-red-500">*</span>
                </Label>
                <div className="flex gap-2">
                  <Input type={showPwd ? "text" : "password"} value={form.password}
                    onChange={(e) => setForm({...form, password: e.target.value})}
                    placeholder="登录密码" className="h-10" />
                  <Button variant="outline" size="icon" onClick={() => setShowPwd(!showPwd)}
                    className="h-10 w-10">
                    {showPwd ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </Button>
                </div>
              </div>
              <div className="space-y-2">
                <Label className="text-sm font-medium">
                  确认密码 <span className="text-red-500">*</span>
                </Label>
                <Input type="password" value={form.confirm_password}
                  onChange={(e) => setForm({...form, confirm_password: e.target.value})}
                  placeholder="再次输入密码" className="h-10" />
              </div>
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium">
                姓名 <span className="text-red-500">*</span>
              </Label>
              <Input value={form.full_name} onChange={(e) => setForm({...form, full_name: e.target.value})}
                placeholder="真实姓名" className="h-10" />
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium flex items-center gap-1">
                <Building className="w-3 h-3" />
                部门 <span className="text-red-500">*</span>
              </Label>
              <Input value={form.department} onChange={(e) => setForm({...form, department: e.target.value})}
                placeholder="所属部门" className="h-10" />
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium flex items-center gap-1">
                <Phone className="w-3 h-3" />
                电话 <span className="text-red-500">*</span>
              </Label>
              <Input value={form.phone} onChange={(e) => setForm({...form, phone: e.target.value})}
                placeholder="联系电话" className="h-10" />
            </div>
            <div className="space-y-2">
              <Label className="text-sm font-medium flex items-center gap-1">
                <Shield className="w-3 h-3" />
                角色 <span className="text-red-500">*</span>
              </Label>
              <select
                value={form.role}
                onChange={(e) => setForm({...form, role: e.target.value})}
                className="h-10 w-full bg-background border border-border rounded-md px-3 text-sm"
              >
                <option value="admin">管理员</option>
                <option value="user">普通用户</option>
              </select>
            </div>
          </div>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => { setShowCreateDialog(false); resetForm(); }}>
              取消
            </Button>
            <Button onClick={handleCreate} disabled={creating}>
              {creating ? <><div className="loading-spinner w-4 h-4 mr-2" /> 创建中...</> : "确认创建"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
