import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import routes from "./routes";
import { AuthProvider } from "@/shared/context/AuthContext";
import { ChatProvider } from "@/shared/context/ChatContext";
import { ProtectedRoute } from "./ProtectedRoute";
import Login from "@/pages/Login";
import NotFound from "@/pages/NotFound";

function App() {
  return (
    <AuthProvider>
      <ChatProvider>
        <BrowserRouter>
          <Toaster position="top-right" richColors closeButton />
          <Routes>
            {/* Public Routes */}
            <Route path="/login" element={<Login />} />

            {/* Protected Routes */}
            <Route element={<ProtectedRoute />}>
              <Route element={<AppShell />}>
                {/* 首页重定向到仪表盘 */}
                <Route path="/" element={<Navigate to="/dashboard" replace />} />
                {routes.map((route) => (
                  <Route
                    key={route.path}
                    path={route.path}
                    element={route.element}
                  />
                ))}
              </Route>
            </Route>

            {/* Catch all */}
            <Route path="*" element={<NotFound />} />
          </Routes>
        </BrowserRouter>
      </ChatProvider>
    </AuthProvider>
  );
}

export default App;
