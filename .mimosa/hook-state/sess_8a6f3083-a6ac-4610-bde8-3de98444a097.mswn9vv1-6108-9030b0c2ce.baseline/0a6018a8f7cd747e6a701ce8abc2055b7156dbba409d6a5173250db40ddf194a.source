/**
 * NotFound Page
 * Enterprise Blue-White UI
 */

import { Link } from "react-router-dom";
import PageMeta from "@/components/layout/PageMeta";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <>
      <PageMeta title="页面未找到" description="" />
      <div className="flex min-h-[70vh] items-center justify-center bg-background px-4">
        <div className="max-w-lg rounded-xl border border-border bg-card p-8 text-center shadow-card">
          <p className="text-sm font-medium text-primary">404</p>
          <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em]">页面不存在</h1>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            你访问的页面不存在，或当前账号没有访问权限。
          </p>
          <Button asChild className="mt-6">
            <Link to="/dashboard">返回仪表盘</Link>
          </Button>
        </div>
      </div>
    </>
  );
}
