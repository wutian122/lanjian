/**
 * Login Page
 * Enterprise Blue-White Design
 */

import { useState, FormEvent, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/shared/context/AuthContext";
import { apiClient } from "@/shared/api/serverClient";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";
import { Lock, User, Bot, Container, FileText, ShieldCheck } from "lucide-react";
import { version } from "../../package.json";

const capabilities = [
  {
    icon: Bot,
    title: "多 Agent 协作",
    description: "Orchestrator、Recon、Analysis、Verification 四 Agent 自主协作，深度审计代码安全",
  },
  {
    icon: Container,
    title: "沙箱 PoC 验证",
    description: "Docker 隔离沙箱中自动生成并执行概念验证，零接触真实环境",
  },
  {
    icon: FileText,
    title: "专业审计报告",
    description: "自动生成 Markdown / JSON / PDF 格式审计报告，支持导出与分享",
  },
];

export default function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [rememberMe, setRememberMe] = useState(false);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const { login, isAuthenticated } = useAuth();

  // REQ-LR-1: 登录成功后固定跳转仪表盘，不再回跳原业务页
  const from = "/dashboard";

  useEffect(() => {
    const savedUsername = localStorage.getItem("remembered_username");
    if (savedUsername) {
      setUsername(savedUsername);
      setRememberMe(true);
    }
  }, []);

  useEffect(() => {
    if (isAuthenticated && !loading) {
      navigate(from, { replace: true });
    }
  }, [isAuthenticated, navigate, from, loading]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const formData = new URLSearchParams();
      formData.append("username", username);
      formData.append("password", password);

      const response = await apiClient.post("/auth/login", formData, {
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
        },
      });

      if (rememberMe) {
        localStorage.setItem("remembered_username", username);
      } else {
        localStorage.removeItem("remembered_username");
      }

      await login(response.data.access_token, response.data.refresh_token, rememberMe);
      toast.success("登录成功");
    } catch (error: any) {
      const detail = error.response?.data?.detail;
      if (Array.isArray(detail)) {
        const messages = detail.map((err: any) => err.msg || err.message || JSON.stringify(err)).join('; ');
        toast.error(messages || "登录失败");
      } else if (typeof detail === 'object') {
        toast.error(detail.msg || detail.message || JSON.stringify(detail));
      } else {
        toast.error(detail || "登录失败，请检查用户名和密码");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50/60 via-white to-slate-50 dark:from-slate-950 dark:via-slate-900 dark:to-slate-950">
      <div className="min-h-screen flex items-center">
        <div className="w-full max-w-6xl mx-auto px-4 py-8 lg:py-12">
          <div className="grid lg:grid-cols-[1.1fr_0.9fr] gap-8 lg:gap-16 items-center">

            {/* Left Column — Brand Narrative */}
            <div className="hidden lg:flex flex-col gap-8">
              {/* Logo & Title */}
              <div className="space-y-4">
                <div className="flex items-center gap-3">
                  <div className="w-14 h-14 rounded-xl flex items-center justify-center bg-primary/10 ring-1 ring-primary/15">
                    <img
                      src="/logo-lanjian.png"
                      alt="蓝鉴"
                      className="w-10 h-10 object-contain"
                    />
                  </div>
                  <div>
                    <h1 className="text-2xl font-bold tracking-tight text-foreground">
                      蓝鉴
                    </h1>
                    <p className="text-sm text-muted-foreground">
                      AI 代码安全审计平台
                    </p>
                  </div>
                </div>
                <h2 className="text-3xl lg:text-4xl font-bold tracking-tight text-foreground leading-tight">
                  企业级 AI 代码安全审计平台
                </h2>
                <p className="text-base text-muted-foreground max-w-md leading-relaxed">
                  基于 Multi-Agent 协作架构，对代码仓库进行深度安全审计、漏洞挖掘与沙箱 PoC 验证，为您的代码安全保驾护航。
                </p>
              </div>

              {/* Capability Cards */}
              <div className="grid gap-4">
                {capabilities.map((item) => {
                  const Icon = item.icon;
                  return (
                    <div
                      key={item.title}
                      className="flex items-start gap-4 p-4 rounded-xl border border-border/60 bg-background/60 backdrop-blur"
                    >
                      <div className="flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center bg-primary/10 text-primary">
                        <Icon className="w-5 h-5" />
                      </div>
                      <div className="space-y-1">
                        <h3 className="text-sm font-semibold text-foreground">
                          {item.title}
                        </h3>
                        <p className="text-sm text-muted-foreground leading-relaxed">
                          {item.description}
                        </p>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Footer */}
              <div className="flex items-center gap-4 text-xs text-muted-foreground">
                <div className="flex items-center gap-1.5">
                  <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />
                  <span>安全连接</span>
                </div>
                <span>v{version}</span>
              </div>
            </div>

            {/* Right Column — Login Form */}
            <div className="w-full max-w-md mx-auto lg:mx-0 lg:ml-auto space-y-6">
              {/* Mobile logo — visible only on small screens */}
              <div className="lg:hidden text-center space-y-2">
                <div className="inline-flex items-center justify-center w-16 h-16 rounded-xl bg-primary/10 ring-1 ring-primary/15">
                  <img
                    src="/logo-lanjian.png"
                    alt="蓝鉴"
                    className="w-11 h-11 object-contain"
                  />
                </div>
                <h1 className="text-xl font-bold tracking-tight text-foreground">
                  蓝鉴
                </h1>
                <p className="text-sm text-muted-foreground">
                  企业级 AI 代码安全审计平台
                </p>
              </div>

              <div className="rounded-xl border bg-background shadow-elevated overflow-hidden mx-4 sm:mx-6 md:mx-0">
                <div className="p-6 sm:p-8">
                  <div className="mb-6 space-y-1">
                    <h2 className="text-xl font-semibold tracking-tight text-foreground">
                      账号登录
                    </h2>
                    <p className="text-sm text-muted-foreground">
                      输入您的凭证以访问平台
                    </p>
                  </div>

                  <form onSubmit={handleSubmit} className="flex flex-col gap-6 sm:gap-5">
                    <div className="space-y-2">
                      <Label htmlFor="username" className="text-sm font-medium">
                        用户名
                      </Label>
                      <div className="relative">
                        <Input
                          id="username"
                          type="text"
                          placeholder="请输入用户名"
                          value={username}
                          onChange={(e) => setUsername(e.target.value)}
                          required
                          className="h-11 rounded-sm pl-10"
                        />
                        <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                      </div>
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="password" className="text-sm font-medium">
                        密码
                      </Label>
                      <div className="relative">
                        <Input
                          id="password"
                          type="password"
                          placeholder="请输入密码"
                          value={password}
                          onChange={(e) => setPassword(e.target.value)}
                          required
                          className="h-11 rounded-sm pl-10"
                        />
                        <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                      </div>
                    </div>

                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        <Checkbox
                          id="remember"
                          checked={rememberMe}
                          onCheckedChange={(checked) =>
                            setRememberMe(checked as boolean)
                          }
                        />
                        <Label
                          htmlFor="remember"
                          className="text-sm font-normal text-muted-foreground cursor-pointer"
                        >
                          记住我
                        </Label>
                      </div>
                    </div>

                    <Button
                      type="submit"
                      className="h-11 w-full rounded-sm font-medium"
                      disabled={loading}
                    >
                      {loading ? (
                        <span className="flex items-center gap-2">
                          <span className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
                          正在登录...
                        </span>
                      ) : (
                        "登录"
                      )}
                    </Button>
                  </form>
                </div>
              </div>

              <p className="text-center text-xs text-muted-foreground">
                蓝鉴 v{version}
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
