/**
 * Sidebar Component
 * Enterprise Blue-White Design
 */

import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import {
    Menu,
    X,
    LayoutDashboard,
    FolderGit2,
    Zap,
    ListTodo,
    Settings,
    Trash2,
    ChevronLeft,
    ChevronRight,
    Github,
    UserCircle,
    Shield,
    MessageSquare,
    Bot,
} from "lucide-react";
import routes from "@/app/routes";
import { version } from "../../../package.json";

const routeIcons: Record<string, React.ReactNode> = {
    "/dashboard": <LayoutDashboard className="w-[18px] h-[18px]" />,
    "/projects": <FolderGit2 className="w-[18px] h-[18px]" />,
    "/instant-analysis": <Zap className="w-[18px] h-[18px]" />,
    "/audit-tasks": <ListTodo className="w-[18px] h-[18px]" />,
    "/ai": <Bot className="w-[18px] h-[18px]" />,
    "/audit-rules": <Shield className="w-[18px] h-[18px]" />,
    "/prompts": <MessageSquare className="w-[18px] h-[18px]" />,
    "/admin": <Settings className="w-[18px] h-[18px]" />,
    "/recycle-bin": <Trash2 className="w-[18px] h-[18px]" />,
};

interface SidebarProps {
    collapsed: boolean;
    setCollapsed: (collapsed: boolean) => void;
    mobileOpen?: boolean;
    setMobileOpen?: (open: boolean) => void;
}

export default function Sidebar({
    collapsed,
    setCollapsed,
    mobileOpen: controlledMobileOpen,
    setMobileOpen: setControlledMobileOpen,
}: SidebarProps) {
    const location = useLocation();
    const [localMobileOpen, setLocalMobileOpen] = useState(false);
    const mobileOpen = controlledMobileOpen ?? localMobileOpen;
    const setMobileOpen = setControlledMobileOpen ?? setLocalMobileOpen;

    const visibleRoutes = routes.filter(route => route.visible !== false);

    return (
        <>
            {/* Mobile Menu Button */}
            <Button
                variant="ghost"
                size="sm"
                className="fixed top-4 left-4 z-50 md:hidden"
                onClick={() => setMobileOpen(!mobileOpen)}
            >
                {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
            </Button>

            {/* Overlay for mobile */}
            {mobileOpen && (
                <button
                    type="button"
                    aria-label="关闭导航菜单"
                    className="fixed inset-0 z-40 bg-slate-950/30 backdrop-blur-sm md:hidden"
                    onClick={() => setMobileOpen(false)}
                />
            )}

            {/* Sidebar */}
            <aside
                className={`
                    fixed left-0 top-0 z-50 h-screen border-r border-border bg-background/95 backdrop-blur transition-all duration-300 ease-in-out
                    ${collapsed ? "w-20" : "w-72"}
                    ${mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"}
                `}
            >
                <div className="flex flex-col h-full">

                    {/* Brand Section */}
                    <div className={`flex-shrink-0 flex items-center h-16 border-b border-border ${collapsed ? 'px-3 justify-center' : 'px-5'}`}>
                        <Link
                            to="/"
                            className={`flex items-center gap-3 group ${collapsed ? 'justify-center' : 'flex-1 min-w-0'}`}
                            onClick={() => setMobileOpen(false)}
                        >
                            <div className="relative flex-shrink-0">
                                <div className="w-12 h-12 rounded-xl flex items-center justify-center bg-primary/10 ring-1 ring-primary/15">
                                    <img
                                        src="/logo-lanjian.png"
                                        alt="蓝鉴"
                                        className="w-8 h-8 object-contain"
                                    />
                                </div>
                            </div>
                            <div className={`transition-all duration-300 ${collapsed ? 'w-0 opacity-0 overflow-hidden' : 'flex-1 min-w-0 opacity-100'}`}>
                                <div className="lanjian-brand-title text-base font-semibold leading-tight tracking-tight">
                                    蓝鉴
                                </div>
                                <div className="text-[11px] text-muted-foreground leading-none mt-0.5">
                                    AI 代码安全审计平台
                                </div>
                            </div>
                        </Link>

                        {/* Collapse button */}
                        <button
                            className={`hidden md:flex absolute -right-3 top-1/2 -translate-y-1/2 w-6 h-6 rounded-md items-center justify-center bg-background border border-border text-muted-foreground hover:bg-primary hover:text-primary-foreground hover:border-primary transition-colors duration-200 ${collapsed ? '' : ''}`}
                            style={{ zIndex: 100 }}
                            onClick={() => setCollapsed(!collapsed)}
                        >
                            {collapsed ? (
                                <ChevronRight className="w-3.5 h-3.5" />
                            ) : (
                                <ChevronLeft className="w-3.5 h-3.5" />
                            )}
                        </button>
                    </div>

                    {/* Navigation */}
                    <nav className="flex-1 min-h-0 py-3 px-3">
                        <div className="space-y-1">
                            {visibleRoutes.map((route) => {
                                const isActive = location.pathname === route.path;
                                return (
                                    <Link
                                        key={route.path}
                                        to={route.path}
                                        className={`
                                            flex items-center gap-3 px-3 py-2.5 transition-colors duration-200 group
                                            ${isActive
                                                ? 'bg-primary text-primary-foreground shadow-sm rounded-full'
                                                : 'text-muted-foreground hover:bg-muted rounded-lg'
                                            }
                                        `}
                                        onClick={() => setMobileOpen(false)}
                                        title={collapsed ? route.name : undefined}
                                    >
                                        <span className="flex-shrink-0">
                                            {routeIcons[route.path] || <LayoutDashboard className="w-[18px] h-[18px]" />}
                                        </span>
                                        {!collapsed && (
                                            <span className={`text-sm ${isActive ? 'font-medium' : 'font-normal'}`}>
                                                {route.name}
                                            </span>
                                        )}
                                    </Link>
                                );
                            })}
                        </div>
                    </nav>

                    {/* Footer */}
                    <div className="flex-shrink-0 p-3 space-y-1 border-t border-border">
                        {/* Theme Toggle */}
                        <ThemeToggle collapsed={collapsed} />

                        {/* Account Link */}
                        <Link
                            to="/account"
                            className={`
                                flex items-center gap-3 px-3 py-2.5 transition-colors duration-200 group
                                ${location.pathname === '/account'
                                    ? 'bg-primary text-primary-foreground shadow-sm rounded-full'
                                    : 'text-muted-foreground hover:bg-muted rounded-lg'
                                }
                            `}
                            onClick={() => setMobileOpen(false)}
                            title={collapsed ? "账号管理" : undefined}
                        >
                            <UserCircle className="w-[18px] h-[18px] flex-shrink-0" />
                            {!collapsed && (
                                <span className="text-sm font-normal">账号管理</span>
                            )}
                        </Link>

                        {/* GitHub & Status Row */}
                        <div className={`flex items-center ${collapsed ? 'flex-col gap-2' : 'justify-between'} px-3 py-2`}>
                            <a
                                href="https://github.com/wutian122/lanjian"
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors"
                                title="GitHub"
                            >
                                <Github className="w-[18px] h-[18px]" />
                                {!collapsed && (
                                    <span className="text-xs text-muted-foreground">v{version}</span>
                                )}
                            </a>

                            {!collapsed && (
                                <div className="flex items-center gap-2">
                                    <div className="relative">
                                        <div className="w-2 h-2 rounded-full bg-emerald-400" />
                                        <div className="absolute inset-0 w-2 h-2 rounded-full bg-emerald-400 animate-ping opacity-50" />
                                    </div>
                                    <span className="text-xs text-emerald-600 dark:text-emerald-400">Online</span>
                                </div>
                            )}

                            {collapsed && (
                                <div className="relative">
                                    <div className="w-2 h-2 rounded-full bg-emerald-400" />
                                    <div className="absolute inset-0 w-2 h-2 rounded-full bg-emerald-400 animate-ping opacity-50" />
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            </aside>
        </>
    );
}
