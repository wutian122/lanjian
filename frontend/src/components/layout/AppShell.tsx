import { useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import routes from "@/app/routes";
import Sidebar from "@/components/layout/Sidebar";
import { MobileTopBar } from "@/components/layout/MobileTopBar";
import { cn } from "@/shared/utils/utils";

function getCurrentRouteName(pathname: string) {
  const currentRoute = routes.find((route) => {
    if (route.path === pathname) {
      return true;
    }

    const basePath = route.path.split("/:", 1)[0];
    return route.path.includes(":") && pathname.startsWith(basePath);
  });

  return currentRoute?.name ?? "蓝鉴";
}

export function AppShell() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const title = getCurrentRouteName(location.pathname);
  const isFullScreenRoute = location.pathname.startsWith("/agent-audit/");

  return (
    <div className={cn(
      "bg-background",
      isFullScreenRoute ? "h-screen overflow-hidden" : "min-h-screen"
    )}>
      <MobileTopBar title={title} onMenuClick={() => setMobileOpen(true)} />
      <Sidebar
        collapsed={collapsed}
        setCollapsed={setCollapsed}
        mobileOpen={mobileOpen}
        setMobileOpen={setMobileOpen}
      />
      <main
        className={cn(
          "transition-all duration-300",
          isFullScreenRoute ? "h-screen overflow-hidden" : "min-h-screen",
          collapsed ? "md:ml-20" : "md:ml-72"
        )}
      >
        <div
          className={
            isFullScreenRoute
              ? "h-full"
              : "mx-auto max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8"
          }
        >
          <Outlet />
        </div>
      </main>
    </div>
  );
}
