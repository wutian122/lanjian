import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '@/shared/context/AuthContext';

export const ProtectedRoute = () => {
  const { isAuthenticated, isLoading, user } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return <div className="flex h-screen items-center justify-center">加载中...</div>;
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // 角色默认首页
  if (location.pathname === "/") {
    const defaultPath = "/dashboard";
    return <Navigate to={defaultPath} replace />;
  }

  return <Outlet />;
};
