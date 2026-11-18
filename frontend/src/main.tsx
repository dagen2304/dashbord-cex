import { StrictMode, Suspense, lazy } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ApiError } from '@/lib/api'
import { AuthProvider, useAuth } from '@/lib/auth'
import { applyTheme, initialTheme } from '@/lib/theme'
import { Layout, RequireRole } from '@/components/Layout'
import { Spinner } from '@/components/ui'
import Login from '@/pages/Login'
import './index.css'

applyTheme(initialTheme())

const Overview = lazy(() => import('@/pages/Overview'))
const DeviceDetail = lazy(() => import('@/pages/DeviceDetail'))
const Alerts = lazy(() => import('@/pages/Alerts'))
const VirtualMachines = lazy(() => import('@/pages/VirtualMachines'))
const Applications = lazy(() => import('@/pages/Applications'))
const Capacity = lazy(() => import('@/pages/Capacity'))
const Explorer = lazy(() => import('@/pages/Explorer'))
const Vrealize = lazy(() => import('@/pages/Vrealize'))
const Inventory = lazy(() => import('@/pages/Inventory'))
const Settings = lazy(() => import('@/pages/Settings'))
const Account = lazy(() => import('@/pages/Account'))

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: true,
      retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
    },
  },
})

function Protected() {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <Spinner label="Vérification de la session…" />
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return <Layout />
}

function App() {
  return (
    <Suspense fallback={<Spinner />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<Protected />}>
          <Route index element={<Overview />} />
          <Route path="devices/:id" element={<DeviceDetail />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="vms" element={<VirtualMachines />} />
          <Route path="applications" element={<Applications />} />
          <Route path="capacity" element={<Capacity />} />
          <Route path="explorer" element={<Explorer />} />
          <Route path="vrealize" element={<Vrealize />} />
          <Route path="inventory" element={<Inventory />} />
          <Route path="settings" element={<RequireRole role="admin"><Settings /></RequireRole>} />
          <Route path="account" element={<Account />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
