// "Health" groups the earlier separate pages as tabs (nothing removed, only regrouped):
// today's status and body map, trends, personal baselines, insights (research), missions, streaks.
import { useState } from 'react'
import { BaselinesPage } from './Baselines'
import { HealthPage } from './Health'
import { MissionsPage } from './Missions'
import { OverviewPage } from './Overview'
import { SciencePage } from './Science'
import { StreaksPage } from './Streaks'

const TABS = [
  ['today', 'Today'],
  ['trends', 'Trends'],
  ['baselines', 'Baselines'],
  ['insights', 'Insights'],
  ['missions', 'Missions'],
  ['streaks', 'Streaks'],
] as const
type Tab = (typeof TABS)[number][0]

export function HealthHub() {
  const [tab, setTab] = useState<Tab>('today')
  return (
    <div className="grid gap-5">
      <nav aria-label="Health sections" className="flex gap-1 overflow-x-auto border-b border-seam">
        {TABS.map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)} aria-current={tab === key ? 'page' : undefined}
            className={`-mb-px shrink-0 border-b-2 px-3 py-2 text-sm ${tab === key ? 'border-signal text-ink' : 'border-transparent text-ink-dim hover:text-ink'}`}>
            {label}
          </button>
        ))}
      </nav>
      {tab === 'today' && <OverviewPage />}
      {tab === 'trends' && <HealthPage />}
      {tab === 'baselines' && <BaselinesPage />}
      {tab === 'insights' && <SciencePage />}
      {tab === 'missions' && <MissionsPage />}
      {tab === 'streaks' && <StreaksPage />}
    </div>
  )
}
