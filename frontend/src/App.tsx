import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Shell } from './components/Shell'
import { AlertsPage } from './pages/Alerts'
import { BaselinesPage } from './pages/Baselines'
import { CompanionPage } from './pages/Companion'
import { HealthPage } from './pages/Health'
import { JournalPage } from './pages/Journal'
import { MissionsPage } from './pages/Missions'
import { OverviewPage } from './pages/Overview'
import { SciencePage } from './pages/Science'
import { SystemPage } from './pages/System'
import { SessionProvider } from './state/session'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <SessionProvider>
        <BrowserRouter>
          <Shell>
            <Routes>
              <Route path="/" element={<OverviewPage />} />
              <Route path="/companion" element={<CompanionPage />} />
              <Route path="/health" element={<HealthPage />} />
              <Route path="/baselines" element={<BaselinesPage />} />
              <Route path="/alerts" element={<AlertsPage />} />
              <Route path="/missions" element={<MissionsPage />} />
              <Route path="/science" element={<SciencePage />} />
              <Route path="/journal" element={<JournalPage />} />
              <Route path="/system" element={<SystemPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Shell>
        </BrowserRouter>
      </SessionProvider>
    </QueryClientProvider>
  )
}
