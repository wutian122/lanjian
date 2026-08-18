/**
 * Agent Error Boundary Component
 * Clean enterprise error display with retry
 */

import { Component, ReactNode } from 'react';
import { AlertTriangle, RefreshCw, ArrowLeft, Bug } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/shared/utils/utils';

interface Props {
  children: ReactNode;
  taskId?: string;
  onRetry?: () => void;
  onReset?: () => void;
  maxRetries?: number;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: React.ErrorInfo | null;
  retryCount: number;
  isRetrying: boolean;
}

export class AgentErrorBoundary extends Component<Props, State> {
  private retryTimeoutId: ReturnType<typeof setTimeout> | null = null;

  constructor(props: Props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      retryCount: 0,
      isRetrying: false,
    };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('[AgentErrorBoundary] Caught error:', error, errorInfo);
    this.setState({ errorInfo });
    this.reportError(error, errorInfo);
  }

  componentWillUnmount() {
    if (this.retryTimeoutId) {
      clearTimeout(this.retryTimeoutId);
    }
  }

  private reportError(error: Error, errorInfo: React.ErrorInfo) {
    const report = {
      timestamp: new Date().toISOString(),
      taskId: this.props.taskId,
      error: { name: error.name, message: error.message, stack: error.stack },
      componentStack: errorInfo.componentStack,
      userAgent: navigator.userAgent,
      url: window.location.href,
    };
    if (import.meta.env.DEV) {
      console.error('[AgentErrorBoundary] Error Report:', report);
    }
  }

  private getErrorCategory(): 'network' | 'stream' | 'render' | 'unknown' {
    const message = this.state.error?.message?.toLowerCase() || '';
    if (message.includes('fetch') || message.includes('network') || message.includes('connection')) return 'network';
    if (message.includes('stream') || message.includes('sse') || message.includes('eventsource')) return 'stream';
    if (message.includes('render') || message.includes('react') || message.includes('component')) return 'render';
    return 'unknown';
  }

  private getRecoveryHint(): string {
    switch (this.getErrorCategory()) {
      case 'network': return '检查网络连接后重试';
      case 'stream': return '实时连接已中断，刷新页面重新连接';
      case 'render': return '显示错误，请尝试刷新页面';
      default: return '发生意外错误';
    }
  }

  handleRetry = async () => {
    const maxRetries = this.props.maxRetries ?? 3;
    if (this.state.retryCount >= maxRetries) return;

    this.setState({ isRetrying: true });
    const delay = Math.min(1000 * Math.pow(2, this.state.retryCount), 10000);
    await new Promise(resolve => { this.retryTimeoutId = setTimeout(resolve, delay); });

    this.setState(prev => ({
      hasError: false, error: null, errorInfo: null,
      retryCount: prev.retryCount + 1, isRetrying: false,
    }));
    this.props.onRetry?.();
  };

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null, retryCount: 0, isRetrying: false });
    this.props.onReset?.();
  };

  handleGoBack = () => { window.history.back(); };
  handleReload = () => { window.location.reload(); };

  render() {
    const { hasError, error, errorInfo, retryCount, isRetrying } = this.state;
    const maxRetries = this.props.maxRetries ?? 3;
    const canRetry = retryCount < maxRetries;
    const category = this.getErrorCategory();

    if (!hasError) return this.props.children;

    return (
      <div className="h-screen bg-background flex items-center justify-center p-4">
        <div className="w-full max-w-lg space-y-6">
          <div className="flex items-center gap-4">
            <div className={cn(
              "p-3 rounded-lg",
              category === 'network' ? 'bg-amber-50 dark:bg-amber-950/20' : 'bg-red-50 dark:bg-red-950/20'
            )}>
              <AlertTriangle className={cn(
                "w-8 h-8",
                category === 'network' ? 'text-amber-600' : 'text-red-600'
              )} />
            </div>
            <div>
              <h2 className="text-xl font-semibold text-foreground">Agent 错误</h2>
              <p className="text-sm text-muted-foreground">{this.getRecoveryHint()}</p>
            </div>
          </div>

          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <div className="px-4 py-3 border-b border-border">
              <span className="text-sm font-medium text-foreground">错误详情</span>
            </div>
            <div className="p-4 space-y-3">
              {error && (
                <div className="space-y-2">
                  <div className="flex items-start gap-2">
                    <Bug className="w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" />
                    <div>
                      <p className="text-sm font-medium text-red-600">{error.name}</p>
                      <p className="text-sm text-foreground">{error.message}</p>
                    </div>
                  </div>
                </div>
              )}

              {this.props.taskId && (
                <div className="text-xs text-muted-foreground">
                  任务 ID: <span className="font-mono">{this.props.taskId}</span>
                </div>
              )}

              {retryCount > 0 && (
                <div className="text-xs text-muted-foreground">
                  重试次数: <span className="text-amber-600">{retryCount}/{maxRetries}</span>
                </div>
              )}

              {import.meta.env.DEV && error?.stack && (
                <details className="text-xs">
                  <summary className="cursor-pointer text-muted-foreground hover:text-foreground">堆栈跟踪</summary>
                  <pre className="mt-2 p-3 bg-muted rounded text-xs text-muted-foreground overflow-auto max-h-40">
                    {error.stack}
                  </pre>
                </details>
              )}
            </div>
          </div>

          <div className="flex gap-3">
            {canRetry && (
              <Button onClick={this.handleRetry} disabled={isRetrying} className="flex-1">
                <RefreshCw className={cn("w-4 h-4 mr-2", isRetrying && "animate-spin")} />
                {isRetrying ? '重试中...' : '重试'}
              </Button>
            )}
            <Button onClick={this.handleGoBack} variant="outline" className="flex-1">
              <ArrowLeft className="w-4 h-4 mr-2" />
              返回
            </Button>
            <Button onClick={this.handleReload} variant="ghost" className="flex-1">
              刷新页面
            </Button>
          </div>

          {!canRetry && (
            <p className="text-center text-xs text-muted-foreground">
              已达最大重试次数，请刷新页面或联系技术支持。
            </p>
          )}
        </div>
      </div>
    );
  }
}

export default AgentErrorBoundary;
