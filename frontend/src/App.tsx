import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { GatewayFlow } from './components/GatewayFlow'
import { Shell } from './components/Shell'
import { AlertsPage } from './pages/Alerts'
import { AmCorePage } from './pages/AmCore'
import { CompanionPage } from './pages/Companion'
import { CrewPage } from './pages/Crew'
import { DiaryPage } from './pages/Diary'
import { EnvironmentPage } from './pages/Environment'
import { HealthHub } from './pages/HealthHub'
import { IntercomPage } from './pages/Intercom'
import { JournalPage } from './pages/Journal'
import { PatientPage } from './pages/Patient'
import { ProfilePage } from './pages/Profile'
import { SettingsHub } from './pages/SettingsHub'
import { AuthGate, useAuth } from './state/auth'
import { SessionProvider } from './state/session'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false } },
})

// Manual measurement forms and End Day live only in the separate Test Console (port 5174).
function AstronautRoutes() {
  return (
    <Routes>
      <Route path="/" element={<CompanionPage />} />
      <Route path="/intercom" element={<IntercomPage />} />
      <Route path="/diary" element={<DiaryPage />} />
      <Route path="/health" element={<HealthHub />} />
      <Route path="/journal" element={<JournalPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

function DoctorRoutes() {
  return (
    <Routes>
      <Route path="/" element={<CrewPage />} />
      <Route path="/patient/:id" element={<PatientPage />} />
      <Route path="/intercom" element={<IntercomPage />} />
      <Route path="/am" element={<AmCorePage />} />
      <Route path="/environment" element={<EnvironmentPage />} />
      <Route path="/alerts" element={<AlertsPage />} />
      <Route path="/settings" element={<SettingsHub />} />
      <Route path="/profile" element={<ProfilePage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

function SignedInApp() {
  const { isDoctor } = useAuth()
  return (
    <SessionProvider>
      <BrowserRouter>
        <Shell>{isDoctor ? <DoctorRoutes /> : <AstronautRoutes />}</Shell>
      </BrowserRouter>
    </SessionProvider>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <GatewayFlow />
      <AuthGate>
        <SignedInApp />
      </AuthGate>
    </QueryClientProvider>
  )
}
