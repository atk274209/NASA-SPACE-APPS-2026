// Doctor-only "Settings": local AI model, data, privacy, sync, backups (Settings) and
// system diagnostics, thresholds and audit trail (System), regrouped as tabs.
import { useState } from 'react'
import { SettingsPage } from './Settings'
import { SystemPage } from './System'

export function SettingsHub() {
  const [tab, setTab] = useState<'settings' | 'system'>('settings')
  return (
    <div className="grid gap-5">
      <nav aria-label="Settings sections" className="flex gap-1 border-b border-seam">
        {([['settings', 'Settings'], ['system', 'System and audit']] as const).map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)} aria-current={tab === key ? 'page' : undefined}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === key ? 'border-signal text-ink' : 'border-transparent text-ink-dim hover:text-ink'}`}>
            {label}
          </button>
        ))}
      </nav>
      {tab === 'settings' ? <SettingsPage /> : <SystemPage />}
    </div>
  )
}
