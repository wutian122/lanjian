/**
 * Connection Status Indicator
 * Clean enterprise connection state display
 */

import { Wifi, WifiOff, RefreshCw, AlertCircle } from 'lucide-react';
import { cn } from '@/shared/utils/utils';
import type { ConnectionState } from '../hooks';

interface ConnectionStatusProps {
  state: ConnectionState;
  reconnectAttempts?: number;
  maxReconnectAttempts?: number;
  className?: string;
}

const STATUS_CONFIG: Record<ConnectionState, {
  icon: typeof Wifi;
  label: string;
  iconColor: string;
  bgColor: string;
  animate?: boolean;
}> = {
  disconnected: {
    icon: WifiOff,
    label: '未连接',
    iconColor: 'text-muted-foreground',
    bgColor: 'bg-muted',
  },
  connecting: {
    icon: RefreshCw,
    label: '连接中',
    iconColor: 'text-amber-600',
    bgColor: 'bg-amber-50 dark:bg-amber-950/20',
    animate: true,
  },
  connected: {
    icon: Wifi,
    label: '已连接',
    iconColor: 'text-emerald-600',
    bgColor: 'bg-emerald-50 dark:bg-emerald-950/20',
  },
  reconnecting: {
    icon: RefreshCw,
    label: '重连中',
    iconColor: 'text-amber-600',
    bgColor: 'bg-amber-50 dark:bg-amber-950/20',
    animate: true,
  },
  failed: {
    icon: AlertCircle,
    label: '连接失败',
    iconColor: 'text-red-600',
    bgColor: 'bg-red-50 dark:bg-red-950/20',
  },
};

export function ConnectionStatus({
  state,
  reconnectAttempts = 0,
  maxReconnectAttempts = 5,
  className,
}: ConnectionStatusProps) {
  const config = STATUS_CONFIG[state];
  const Icon = config.icon;

  return (
    <div className={cn('flex items-center gap-1.5', className)}>
      <div className={cn(
        'flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border',
        config.bgColor,
        config.iconColor
      )}>
        <Icon className={cn('w-3 h-3', config.animate && 'animate-spin')} />
        <span>{config.label}</span>
        {state === 'reconnecting' && reconnectAttempts > 0 && (
          <span className="opacity-70">
            ({reconnectAttempts}/{maxReconnectAttempts})
          </span>
        )}
      </div>

      {state === 'connected' && (
        <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse" />
      )}
    </div>
  );
}

export default ConnectionStatus;
