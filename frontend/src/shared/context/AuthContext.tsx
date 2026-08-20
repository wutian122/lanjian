import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { apiClient } from '../api/serverClient';

interface User {
  id: string;
  username: string;
  email?: string;
  full_name: string;
  role: string;
  department?: string;
  avatar_url?: string;
}

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (accessToken: string, refreshToken: string, rememberMe?: boolean) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const checkAuth = async () => {
      // Check both localStorage (remember me) and sessionStorage (session only)
      const token = localStorage.getItem('access_token') || sessionStorage.getItem('access_token');
      if (token) {
        try {
          const response = await apiClient.get('/users/me');
          setUser(response.data);
          setIsAuthenticated(true);
        } catch (error) {
          console.error('Auth check failed', error);
          logout();
        }
      }
      setIsLoading(false);
    };

    checkAuth();
  }, []);

  const login = async (accessToken: string, refreshToken: string, rememberMe: boolean = false) => {
    // Clear any existing tokens first
    localStorage.removeItem('access_token');
    sessionStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    sessionStorage.removeItem('refresh_token');

    // Store tokens based on rememberMe preference
    const storage = rememberMe ? localStorage : sessionStorage;
    storage.setItem('access_token', accessToken);
    if (refreshToken) {
      storage.setItem('refresh_token', refreshToken);
    }

    try {
        const response = await apiClient.get('/users/me');
        setUser(response.data);
        setIsAuthenticated(true);
    } catch (e) {
        console.error("Login fetch user failed", e);
    }
  };

  const logout = () => {
    const token = localStorage.getItem('access_token') || sessionStorage.getItem('access_token');
    const refresh = localStorage.getItem('refresh_token') || sessionStorage.getItem('refresh_token');
    // A2: 通知后端将令牌加入黑名单（keepalive 保证页面跳转后请求仍送达）。
    // 登出端点不需要登录态，access 过期后也能正常拉黑 refresh。
    if (token || refresh) {
      try {
        fetch('/api/v1/auth/logout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refresh, access_token: token }),
          keepalive: true,
        }).catch(() => {});
      } catch (e) {
        console.error('Logout API call failed', e);
      }
    }
    localStorage.removeItem('access_token');
    sessionStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    sessionStorage.removeItem('refresh_token');
    setUser(null);
    setIsAuthenticated(false);
  };

  return (
    <AuthContext.Provider value={{ user, isAuthenticated, isLoading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

