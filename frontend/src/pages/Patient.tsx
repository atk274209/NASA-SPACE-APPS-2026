// Doctor's patient detail: one astronaut's reported problems, vitals, baselines, journal and insights.
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { NotesList } from '../components/Capture'
import { RoutineExams } from '../components/Exams'
import { ExplainBox } from '../components/Explain'
import { LiveFeed } from '../components/Feed'
import { useSession } from '../state/session'
import { HealthHub } from './HealthHub'
import { JournalPage } from './Journal'

type Tab = 'problems' | 'notes' | 'exams' | 'health' | 'journal'

export function PatientPage() {
  const { id } = useParams()
  const astronautId = Number(id)
  const { astronaut, selectAstronaut } = useSession()
  const [tab, setTab] = useState<Tab>('problems')
  useEffect(() => {
    if (astronautId && astronaut?.id !== astronautId) selectAstronaut(astronautId)
  }, [astronautId, astronaut?.id, selectAstronaut])
  const tabs: [Tab, string][] = [['problems', 'Reported problems'], ['notes', "AM's notes"], ['exams', 'Exams'], ['health', 'Health'], ['journal', 'Journal']]
  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold">
          {astronaut?.id === astronautId ? astronaut.display_name : 'Crew member'}{' '}
          <span className="num text-base text-ink-faint">{astronaut?.id === astronautId ? astronaut.code : ''}</span>
        </h1>
        <Link to="/" className="text-sm text-ink-dim hover:text-ink">← Crew</Link>
      </div>
      <nav aria-label="Patient detail" className="flex gap-1 border-b border-seam">
        {tabs.map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)} aria-current={tab === key ? 'page' : undefined}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === key ? 'border-signal text-ink' : 'border-transparent text-ink-dim hover:text-ink'}`}>
            {label}
          </button>
        ))}
      </nav>
      {astronaut?.id === astronautId && (
        <>
          {tab === 'problems' && <div className="grid gap-4"><ExplainBox astronautId={astronautId} /><LiveFeed astronautId={astronautId} /></div>}
          {tab === 'notes' && <NotesList astronautId={astronautId} />}
          {tab === 'exams' && <RoutineExams astronautId={astronautId} canEnter={false} />}
          {tab === 'health' && <HealthHub />}
          {tab === 'journal' && <JournalPage />}
        </>
      )}
    </div>
  )
}
