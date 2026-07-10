/**
 * React错误边界组件
 * 捕获组件树中的JavaScript错误并记录
 */

import React, { Component, ReactNode } from 'react';
import { logger, LogCategory } from '@/shared/utils/logger';
import { Button } from '@/components/ui/button';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  onError?: (error: Error, errorInfo: React.ErrorInfo) => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: React.ErrorInfo | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
    };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return {
      hasError: true,
      error,
    };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    // 记录错误到日志系统
    logger.error(
      LogCategory.CONSOLE_ERROR,
      `React组件错误: ${error.message}`,
      {
        error: error.toString(),
        componentStack: errorInfo.componentStack,
      },
      error.stack
    );

    this.setState({
      errorInfo,
    });

    // 调用自定义错误处理
    this.props.onError?.(error, errorInfo);
  }

  handleReset = () => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
    });
  };

  handleReload = () => {
    window.location.reload();
  };

  handleGoHome = () => {
    window.location.href = '/';
  };

  render() {
    if (this.state.hasError) {
      // 如果提供了自定义fallback，使用它
      if (this.props.fallback) {
        return this.props.fallback;
      }

      // 默认错误UI
      return (
        <div className="flex min-h-screen items-center justify-center bg-background px-4">
          <div className="max-w-xl rounded-xl border border-border bg-card p-8 shadow-elevated">
            <p className="text-sm font-medium text-destructive">系统异常</p>
            <h1 className="mt-3 text-2xl font-semibold">页面渲染失败</h1>
            <p className="mt-3 text-sm leading-6 text-muted-foreground">
              请刷新页面重试。如果问题持续出现，请联系管理员并提供当前操作路径。
            </p>
            <div className="mt-6 flex gap-3">
              <Button onClick={this.handleReload}>刷新页面</Button>
              <Button variant="outline" onClick={this.handleGoHome}>返回仪表盘</Button>
            </div>
            {import.meta.env.DEV && this.state.error && (
              <details className="mt-6">
                <summary className="cursor-pointer text-xs text-muted-foreground">技术详情</summary>
                <pre className="mt-2 rounded-sm bg-slate-950 p-3 text-xs leading-5 text-slate-100 overflow-auto max-h-60">
                  {this.state.error.stack || this.state.error.message}
                </pre>
              </details>
            )}
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

/**
 * 高阶组件：为组件添加错误边界
 */
export function withErrorBoundary<P extends object>(
  Component: React.ComponentType<P>,
  fallback?: ReactNode
) {
  return function WithErrorBoundaryComponent(props: P) {
    return (
      <ErrorBoundary fallback={fallback}>
        <Component {...props} />
      </ErrorBoundary>
    );
  };
}
