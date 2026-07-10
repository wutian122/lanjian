/**
 * Splash Screen Component
 * Clean enterprise welcome screen
 */

import { useEffect, useRef, useState } from "react";
import { Shield, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";

interface SplashScreenProps {
  onComplete: () => void;
}

export function SplashScreen({ onComplete }: SplashScreenProps) {
  const [showLogo, setShowLogo] = useState(false);
  const [showActions, setShowActions] = useState(false);
  const initializedRef = useRef(false);
  const onCompleteRef = useRef(onComplete);

  useEffect(() => {
    onCompleteRef.current = onComplete;
  }, [onComplete]);

  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;

    setTimeout(() => setShowLogo(true), 100);
    setTimeout(() => setShowActions(true), 600);
  }, []);

  return (
    <div className="h-screen bg-background flex flex-col items-center justify-center p-4">
      <div className={`text-center mb-12 transition-all duration-700 ${showLogo ? "opacity-100 translate-y-0" : "opacity-0 -translate-y-6"}`}>
        <div className="text-5xl sm:text-6xl font-bold tracking-tight mb-3">
          <span className="text-primary">蓝鉴</span>
          <span className="text-foreground"> · lanjian</span>
        </div>
        <div className="flex items-center justify-center gap-3 text-muted-foreground text-sm mt-3">
          <div className="w-10 h-px bg-border" />
          <Shield className="w-4 h-4" />
          <span>自主安全审计平台</span>
          <Shield className="w-4 h-4" />
          <div className="w-10 h-px bg-border" />
        </div>
        <div className="mt-2 text-xs text-muted-foreground">
          v3.0.0 · Multi-Agent 协作审计引擎
        </div>
      </div>

      <div className={`w-full max-w-md transition-all duration-700 delay-200 ${showActions ? "opacity-100 translate-y-0" : "opacity-0 translate-y-6"}`}>
        <div className="rounded-xl border border-border bg-card p-8 text-center shadow-sm">
          <div className="w-14 h-14 bg-primary/10 rounded-xl flex items-center justify-center mx-auto mb-5">
            <Zap className="w-7 h-7 text-primary" />
          </div>
          <h2 className="text-xl font-semibold text-foreground mb-2">开始安全审计</h2>
          <p className="text-sm text-muted-foreground mb-6">
            lanjian 将使用多个 AI Agent 协作对您的代码进行深度安全审计，发现潜在漏洞并提供修复建议。
          </p>
          <Button size="lg" className="w-full" onClick={() => onCompleteRef.current()}>
            创建审计任务
          </Button>
        </div>
      </div>

      <div className="mt-8 text-xs text-muted-foreground">
        由 lanjian Multi-Agent 引擎驱动
      </div>
    </div>
  );
}

export default SplashScreen;