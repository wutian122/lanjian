/**
 * Account Page
 * Enterprise Blue-White UI
 */

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import {
  User,
  Phone,
  Shield,
  Calendar,
  Save,
  KeyRound,
  LogOut,
  UserPlus,
  GitBranch,
  Building,
} from "lucide-react";
import { apiClient } from "@/shared/api/serverClient";
import { useAuth } from "@/shared/context/AuthContext";
import { toast } from "sonner";
import { Separator } from "@/components/ui/separator";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionPanel } from "@/components/ui/section-panel";
import type { Profile } from "@/shared/types";

export default function Account() {
  const { logout } = useAuth();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showLogoutDialog, setShowLogoutDialog] = useState(false);
  const [form, setForm] = useState({
    full_name: "",
    department: "",
    phone: "",
    github_username: "",
    gitlab_username: "",
  });
  const [passwordForm, setPasswordForm] = useState({
    current_password: "",
    new_password: "",
    confirm_password: "",
  });
  const [changingPassword, setChangingPassword] = useState(false);

  useEffect(() => {
    loadProfile();
  }, []);

  const loadProfile = async () => {
    try {
      setLoading(true);
      const res = await apiClient.get('/users/me');
      setProfile(res.data);
      setForm({
        full_name: res.data.full_name || "",
        department: res.data.department || "",
        phone: res.data.phone || "",
        github_username: res.data.github_username || "",
        gitlab_username: res.data.gitlab_username || "",
      });
    } catch (error) {
      console.error('Failed to load profile:', error);
      toast.error("加载账号信息失败");
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    try {
      setSaving(true);
      const res = await apiClient.put('/users/me', form);
      setProfile(res.data);
      toast.success("账号信息已更新");
    } catch (error) {
      console.error('Failed to update profile:', error);
      toast.error("更新失败");
    } finally {
      setSaving(false);
    }
  };

  const handleChangePassword = async () => {
    if (!passwordForm.new_password || !passwordForm.confirm_password) {
      toast.error("请填写新密码");
      return;
    }
    if (passwordForm.new_password !== passwordForm.confirm_password) {
      toast.error("两次输入的密码不一致");
      return;
    }
    if (passwordForm.new_password.length < 6) {
      toast.error("密码长度至少6位");
      return;
    }

    try {
      setChangingPassword(true);
      await apiClient.put('/users/me', { password: passwordForm.new_password });
      toast.success("密码已更新");
      setPasswordForm({ current_password: "", new_password: "", confirm_password: "" });
    } catch (error) {
      console.error('Failed to change password:', error);
      toast.error("密码更新失败");
    } finally {
      setChangingPassword(false);
    }
  };

  const formatDate = (dateString?: string) => {
    if (!dateString) return "-";
    return new Date(dateString).toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: 'long',
      day: 'numeric'
    });
  };

  const getInitials = (name?: string, email?: string) => {
    if (name) return name.charAt(0).toUpperCase();
    if (email) return email.charAt(0).toUpperCase();
    return "U";
  };

  const handleLogout = () => {
    logout();
    toast.success("已退出登录");
    window.location.href = '/login';
  };

  const handleSwitchAccount = () => {
    logout();
    window.location.href = '/login';
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
        eyebrow="账号管理"
        title="账号管理"
        description="管理个人资料和登录安全。"
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Profile Card */}
        <SectionPanel title="用户信息">
          <div className="text-center">
            <div className="relative inline-block mb-4">
              <Avatar className="w-24 h-24 border-2 border-primary/30">
                <AvatarImage src={profile?.avatar_url} />
                <AvatarFallback className="bg-primary/10 text-primary text-2xl font-bold">
                  {getInitials(profile?.full_name, profile?.email)}
                </AvatarFallback>
              </Avatar>
              <div className="absolute -bottom-1 -right-1 w-6 h-6 bg-emerald-500 rounded-full border-2 border-background flex items-center justify-center">
                <div className="w-2 h-2 bg-white rounded-full animate-pulse" />
              </div>
            </div>
            <h4 className="text-lg font-semibold text-foreground mb-1">
              {profile?.full_name || "未设置姓名"}
            </h4>
            <p className="text-muted-foreground text-sm">{profile?.username || "-"}</p>

            <div className="mt-6 pt-6 border-t border-border space-y-3 text-left">
              <div className="flex items-center gap-3 text-sm">
                <Shield className="w-4 h-4 text-violet-600" />
                <span className="text-muted-foreground">角色:</span>
                <span className="text-violet-600 font-semibold">
                  {profile?.role === 'super_admin' ? '超级管理员' :
                   profile?.role === 'admin' ? '管理员' : '普通用户'}
                </span>
              </div>
              <div className="flex items-center gap-3 text-sm">
                <Building className="w-4 h-4 text-emerald-600" />
                <span className="text-muted-foreground">部门:</span>
                <span className="text-foreground">{profile?.department || "-"}</span>
              </div>
              <div className="flex items-center gap-3 text-sm">
                <Calendar className="w-4 h-4 text-sky-600" />
                <span className="text-muted-foreground">注册时间:</span>
                <span className="text-foreground">{formatDate(profile?.created_at)}</span>
              </div>
            </div>

            <div className="mt-6 pt-6 border-t border-border space-y-2">
              <Button
                variant="outline"
                onClick={handleSwitchAccount}
                className="w-full h-10"
              >
                <UserPlus className="w-4 h-4 mr-2" />
                切换账号
              </Button>
              <Button
                variant="destructive"
                onClick={() => setShowLogoutDialog(true)}
                className="w-full h-10"
              >
                <LogOut className="w-4 h-4 mr-2" />
                退出登录
              </Button>
            </div>
          </div>
        </SectionPanel>

        {/* Edit Form */}
        <SectionPanel title="基本信息" className="lg:col-span-2">
          <div className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="username" className="text-sm font-medium flex items-center gap-2">
                  <User className="w-3 h-3" /> 用户名
                </Label>
                <Input
                  id="username"
                  value={profile?.username || ""}
                  disabled
                  className="bg-muted text-muted-foreground cursor-not-allowed"
                />
                <p className="text-xs text-muted-foreground">用户名不可修改</p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="full_name" className="text-sm font-medium flex items-center gap-2">
                  <User className="w-3 h-3" /> 姓名
                </Label>
                <Input
                  id="full_name"
                  value={form.full_name}
                  onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                  placeholder="请输入姓名"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="department" className="text-sm font-medium flex items-center gap-2">
                  <Building className="w-3 h-3" /> 部门
                </Label>
                <Input
                  id="department"
                  value={form.department}
                  onChange={(e) => setForm({ ...form, department: e.target.value })}
                  placeholder="请输入部门"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="phone" className="text-sm font-medium flex items-center gap-2">
                  <Phone className="w-3 h-3" /> 手机号
                </Label>
                <Input
                  id="phone"
                  value={form.phone}
                  onChange={(e) => setForm({ ...form, phone: e.target.value })}
                  placeholder="请输入手机号"
                />
              </div>
            </div>

            <div className="pt-6 border-t border-border">
              <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
                <GitBranch className="w-4 h-4" />
                代码托管账号
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="github" className="text-sm font-medium flex items-center gap-2">
                    <GitBranch className="w-3 h-3" /> GitHub 用户名
                  </Label>
                  <Input
                    id="github"
                    value={form.github_username}
                    onChange={(e) => setForm({ ...form, github_username: e.target.value })}
                    placeholder="your-github-username"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="gitlab" className="text-sm font-medium flex items-center gap-2">
                    <GitBranch className="w-3 h-3" /> GitLab 用户名
                  </Label>
                  <Input
                    id="gitlab"
                    value={form.gitlab_username}
                    onChange={(e) => setForm({ ...form, gitlab_username: e.target.value })}
                    placeholder="your-gitlab-username"
                  />
                </div>
              </div>
            </div>

            <div className="flex justify-end pt-4">
              <Button onClick={handleSave} disabled={saving} className="h-10">
                {saving ? (
                  <>
                    <div className="loading-spinner w-4 h-4 mr-2" />
                    保存中...
                  </>
                ) : (
                  <>
                    <Save className="w-4 h-4 mr-2" />
                    保存修改
                  </>
                )}
              </Button>
            </div>
          </div>
        </SectionPanel>

        <div className="lg:col-span-3">
          <Separator />
        </div>

        {/* Password Change */}
        <SectionPanel title="修改密码" className="lg:col-span-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label htmlFor="new_password" className="text-sm font-medium">新密码</Label>
              <Input
                id="new_password"
                type="password"
                value={passwordForm.new_password}
                onChange={(e) => setPasswordForm({ ...passwordForm, new_password: e.target.value })}
                placeholder="输入新密码"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm_password" className="text-sm font-medium">确认密码</Label>
              <Input
                id="confirm_password"
                type="password"
                value={passwordForm.confirm_password}
                onChange={(e) => setPasswordForm({ ...passwordForm, confirm_password: e.target.value })}
                placeholder="再次输入新密码"
              />
            </div>
            <div className="flex items-end">
              <Button
                onClick={handleChangePassword}
                disabled={changingPassword}
                variant="outline"
                className="h-10"
              >
                {changingPassword ? (
                  <>
                    <div className="loading-spinner w-4 h-4 mr-2" />
                    更新中...
                  </>
                ) : (
                  <>
                    <KeyRound className="w-4 h-4 mr-2" />
                    更新密码
                  </>
                )}
              </Button>
            </div>
          </div>
        </SectionPanel>
      </div>

      {/* Logout Confirmation Dialog */}
      <AlertDialog open={showLogoutDialog} onOpenChange={setShowLogoutDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="text-lg font-semibold text-foreground flex items-center gap-2">
              <LogOut className="w-5 h-5 text-red-500" />
              确认退出登录？
            </AlertDialogTitle>
            <AlertDialogDescription className="text-muted-foreground">
              退出后需要重新登录才能访问系统。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleLogout}
              className="bg-red-600 hover:bg-red-700 text-white"
            >
              确认退出
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
