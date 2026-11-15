// The astronaut's private diary: a daily conversation with AMC, a colour for each day, and the
// earlier pages to read back. Nobody else reads it during the mission; only a medical matter
// leaves it (an urgent one at once, anything else only if the astronaut chooses to send it).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { diaryApi, type DiaryDay, type DiaryForward, type DiaryLevel, type DiaryPage } from '../api/diary'
import { ErrorNote, Loading } from '../components/ui'

const LEVEL: Record<DiaryLevel, { label: string; color: string }> = {
  green: { label: 'A good day', color: 'var(--color-sev-normal)' },
  yellow: { label: 'A difficult day', color: 'var(--color-sev-watch)' },
  red: { label: 'A very hard day', color: 'var(--color-sev-critical)' },
  none: { label: 'Not written', color: 'var(--color-ink-faint)' },
}

function dayTitle(d: DiaryDay, long = false) {
  const date = new Date(`${d.day_date}T12:00:00Z`)
  const text = date.toLocaleDateString('en-GB', long
    ? { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' }
    : { day: 'numeric', month: 'short', timeZone: 'UTC' })
  return d.is_today ? (long ? `Today, ${text}` : 'Today') : text
}

function Dot({ level, size = 10 }: { level: DiaryLevel; size?: number }) {
  return <span aria-hidden className="inline-block shrink-0 rounded-full" style={{ width: size, height: size, background: LEVEL[level].color }} />
}

function DayList({ days, current, onPick }: { days: DiaryDay[]; current: number | null; onPick: (id: number) => void }) {
  return (
    <nav aria-label="Diary pages" className="min-w-0">
      <ol className="flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">
        {days.map((d) => (
          <li key={d.id} className="shrink-0">
            <button
              onClick={() => onPick(d.id)}
              aria-current={d.id === current ? 'page' : undefined}
              className={`flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm transition-colors ${
                d.id === current ? 'bg-deck text-ink' : 'text-ink-dim hover:bg-deck/60 hover:text-ink'}`}
            >
              <Dot level={d.level} />
              <span className="whitespace-nowrap">{dayTitle(d)}</span>
              {d.mission_day != null && <span className="num ml-auto pl-3 text-xs text-ink-faint">Day {d.mission_day}</span>}
              <span className="sr-only">{LEVEL[d.level].label}</span>
            </button>
          </li>
        ))}
      </ol>
    </nav>
  )
}

function Forward({ f, dayId }: { f: DiaryForward; dayId: number }) {
  const qc = useQueryClient()
  const [severity, setSeverity] = useState('')
  const decide = useMutation({
    mutationFn: (send: boolean) => diaryApi.decide(f.id, send, send ? severity : undefined),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['diary', 'page', dayId] }),
  })
  if (f.status === 'sent_urgent') {
    return <p className="text-sm text-sev-critical">{f.label}: sent to your doctor at once, with only this sentence. It waits for their approval.</p>
  }
  if (f.status === 'sent') return <p className="text-sm text-ink-dim">{f.label}: sent to your doctor.</p>
  if (f.status === 'kept_private') return <p className="text-sm text-ink-faint">{f.label}: kept private.</p>
  return (
    <div className="grid gap-2 rounded-md border border-seam-strong bg-deck/60 p-3 text-sm">
      <p><span className="font-medium text-ink">{f.label}</span> <span className="text-ink-dim">in “{f.quote}”</span></p>
      <p className="text-ink-dim">This stays in your diary unless you send it. Your doctor would see only the symptom and how strong it is.</p>
      <div className="flex flex-wrap items-center gap-2">
        <select aria-label="How strong is it" value={severity} onChange={(e) => setSeverity(e.target.value)}
          className="h-8 rounded border border-seam-strong bg-deck px-2 text-ink">
          <option value="">How strong is it?</option>
          <option value="mild">Mild</option>
          <option value="moderate">Moderate</option>
          <option value="severe">Severe</option>
        </select>
        <button className="h-8 rounded bg-ink px-3 font-semibold text-void disabled:opacity-50" disabled={!severity || decide.isPending}
          onClick={() => decide.mutate(true)}>Send to my doctor</button>
        <button className="h-8 rounded border border-seam-strong px-3 text-ink hover:border-signal disabled:opacity-50" disabled={decide.isPending}
          onClick={() => decide.mutate(false)}>Keep private</button>
      </div>
      {decide.error && <ErrorNote error={decide.error} />}
    </div>
  )
}

function DayColour({ page }: { page: DiaryPage }) {
  const qc = useQueryClient()
  const d = page.day
  const analyse = useMutation({
    mutationFn: () => diaryApi.analyse(d.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['diary'] })
    },
  })
  const wrote = page.messages.some((m) => m.role === 'user')
  return (
    <section aria-label="How the day went" className="grid gap-2 border-t border-seam pt-4">
      <div className="flex flex-wrap items-center gap-3">
        <Dot level={wrote ? d.level : 'none'} size={12} />
        <span className="font-medium">{wrote ? LEVEL[d.level].label : 'Nothing written yet'}</span>
        {wrote && (
          <button onClick={() => analyse.mutate()} disabled={analyse.isPending}
            className="ml-auto h-8 rounded border border-seam-strong px-3 text-sm text-ink hover:border-signal disabled:opacity-50">
            {analyse.isPending ? 'Reading your page…' : d.analysed_at ? 'Read my page again' : 'Look back on this day'}
          </button>
        )}
      </div>
      {d.summary && <p className="max-w-prose text-ink">{d.summary}</p>}
      {wrote && d.ai_status === 'unavailable' && (
        <p className="text-sm text-ink-faint">The colour comes from the words you used. A fuller look back needs the local AI, which is offline.</p>
      )}
      {analyse.error && <ErrorNote error={analyse.error} />}
    </section>
  )
}

function Conversation({ page }: { page: DiaryPage }) {
  const qc = useQueryClient()
  const [text, setText] = useState('')
  const end = useRef<HTMLDivElement>(null)
  const write = useMutation({
    mutationFn: () => diaryApi.write(page.day.id, text.trim()),
    onSuccess: (p) => {
      setText('')
      qc.setQueryData(['diary', 'page', p.day.id], p)
      qc.invalidateQueries({ queryKey: ['diary', 'days'] })
    },
  })
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }) }, [page.messages.length])
  const byMessage = new Map<number, DiaryForward[]>()
  page.forwards.forEach((f) => byMessage.set(f.message_id, [...(byMessage.get(f.message_id) ?? []), f]))
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (text.trim() && !write.isPending) write.mutate()
  }
  return (
    <div className="grid gap-4">
      <ol className="grid gap-4" aria-live="polite">
        {page.messages.map((m) => (
          <li key={m.id} className={m.role === 'user' ? 'justify-self-end' : 'justify-self-start'}>
            {m.role === 'user' ? (
              <div className="max-w-[60ch] rounded-lg rounded-br-sm bg-deck px-4 py-2.5 text-ink">{m.content}</div>
            ) : (
              <div className="max-w-[60ch] whitespace-pre-line border-l-2 border-signal/60 pl-4 text-ink">{m.content}</div>
            )}
            {(byMessage.get(m.id) ?? []).map((f) => <div key={f.id} className="mt-2 max-w-[60ch]"><Forward f={f} dayId={page.day.id} /></div>)}
          </li>
        ))}
        {write.isPending && <li className="justify-self-start pl-4 text-sm text-ink-faint">AMC is listening…</li>}
      </ol>
      <div ref={end} />
      {page.day.is_today ? (
        <form onSubmit={submit} className="grid gap-2">
          <label className="sr-only" htmlFor="diary-input">Write in your diary</label>
          <textarea id="diary-input" rows={3} value={text} onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) submit(e) }}
            placeholder="Write about your day…"
            className="w-full resize-y rounded-md border border-seam-strong bg-deck px-3 py-2 text-ink placeholder:text-ink-faint focus:border-signal focus:outline-none" />
          <div className="flex items-center justify-between gap-3 text-xs text-ink-faint">
            <span>Enter to send, Shift+Enter for a new line.</span>
            <button className="h-9 rounded-md bg-ink px-4 text-sm font-semibold text-void disabled:opacity-50" disabled={!text.trim() || write.isPending}>
              Write
            </button>
          </div>
          {write.error && <ErrorNote error={write.error} />}
        </form>
      ) : (
        <p className="text-sm text-ink-faint">Earlier pages can be read, not changed.</p>
      )}
    </div>
  )
}

export function DiaryPage() {
  const today = useQuery({ queryKey: ['diary', 'today'], queryFn: diaryApi.today })
  const days = useQuery({ queryKey: ['diary', 'days'], queryFn: diaryApi.days, enabled: today.isSuccess })
  const [picked, setPicked] = useState<number | null>(null)
  const current = picked ?? today.data?.day.id ?? null
  const page = useQuery({
    queryKey: ['diary', 'page', current],
    queryFn: () => diaryApi.page(current!),
    enabled: current != null,
    initialData: current === today.data?.day.id ? today.data : undefined,
  })

  if (today.isError) return <ErrorNote error={today.error} />
  if (!today.data) return <Loading />
  return (
    <div className="grid gap-6">
      <header className="grid gap-2">
        <h1 className="text-[1.75rem]">Diary</h1>
        <p className="max-w-[68ch] text-ink-dim">
          Talk with AMC about your day. Only you can read this diary until the mission is back on Earth.
          If you write about a medical problem, AMC tells you before anything leaves it.
        </p>
      </header>
      <div className="grid gap-6 lg:grid-cols-[14rem_minmax(0,1fr)]">
        {days.data ? <DayList days={days.data} current={current} onPick={setPicked} /> : <div />}
        <article className="grid min-w-0 gap-6" aria-label="Diary page">
          {page.data && (
            <>
              <h2 className="text-lg font-semibold">
                {dayTitle(page.data.day, true)}
                {page.data.day.mission_day != null && <span className="num ml-3 text-sm font-normal text-ink-faint">Mission day {page.data.day.mission_day}</span>}
              </h2>
              <Conversation page={page.data} />
              <DayColour page={page.data} />
            </>
          )}
          {page.isError && <ErrorNote error={page.error} />}
        </article>
      </div>
    </div>
  )
}
