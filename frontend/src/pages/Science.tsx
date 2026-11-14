import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, type DailySeriesOut, type Finding, type Observation } from '../api/client'
import { Button, Empty, ErrorNote, inputCls, Loading, Section, SimulatedMark } from '../components/ui'
import { fmtDateTime, fmtNum, titleCase, unitLabel } from '../lib/format'
import { useSession } from '../state/session'
import { NoCrew } from './Overview'

const TABS = [
  { key: 'patterns', label: 'Patterns' },
  { key: 'observations', label: 'Research observations' },
  { key: 'series', label: 'Daily series' },
  { key: 'library', label: 'Literature' },
] as const

const TYPE_LABEL: Record<string, string> = {
  trend: 'Trend',
  correlation: 'Same-day association',
  temporal_association: 'Temporal association',
}

function FindingRow({ f, onDraft, drafting }: { f: Finding; onDraft: () => void; drafting: boolean }) {
  return (
    <li className="border-b border-seam/60 py-3 last:border-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-sm font-medium text-signal">{TYPE_LABEL[f.finding_type]}</span>
        {f.finding_type !== 'trend' && (
          f.detrended_supported
            ? <span className="text-xs text-sev-normal">Persists after removing shared trends</span>
            : <span className="text-xs text-sev-watch">Shared trend only, may be coincidental</span>
        )}
        <span className="num ml-auto text-xs text-ink-faint">rho {f.rho >= 0 ? '+' : ''}{f.rho.toFixed(2)}, q {f.q_value.toFixed(3)}, n {f.n_days}</span>
      </div>
      <p className="mt-1 text-sm text-ink">{f.summary}</p>
      <div className="mt-2">
        <Button variant="quiet" onClick={onDraft} disabled={drafting}>{drafting ? 'Drafting…' : 'Draft research observation'}</Button>
      </div>
    </li>
  )
}

function Patterns({ astronautId, onDrafted }: { astronautId: number; onDrafted: () => void }) {
  const qc = useQueryClient()
  const latest = useQuery({ queryKey: ['run', astronautId], queryFn: () => api.latestRun(astronautId) })
  const run = useMutation({ mutationFn: () => api.runCorrelation(astronautId), onSuccess: (r) => qc.setQueryData(['run', astronautId], r) })
  const draft = useMutation({
    mutationFn: (id: number) => api.createObservation(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['observations', astronautId] }); onDrafted() },
  })
  const r = latest.data
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-4">
        <p className="mr-auto max-w-[72ch] text-sm text-ink-dim">
          Screens every daily series for monotonic trends and for associations between series at 0 to 3 day lags
          (Spearman), with Benjamini-Hochberg false-discovery control. Each association is re-tested on day-to-day
          changes to separate real co-movement from two unrelated trends.
        </p>
        <Button onClick={() => run.mutate()} disabled={run.isPending}>{run.isPending ? 'Analysing…' : 'Run analysis'}</Button>
      </div>
      {(run.error || draft.error) && <ErrorNote error={run.error ?? draft.error} />}
      {latest.isLoading ? <Loading /> : !r ? (
        <Empty title="No analysis yet">Run the analysis to screen this astronaut&apos;s history.</Empty>
      ) : (
        <Section title={`${r.findings.length} patterns`} aside={<span className="num">{r.n_tests} tests on {r.n_series} series through day {r.through_day}, {fmtDateTime(r.created_at)}</span>}>
          {r.findings.length === 0 ? <p className="text-sm text-ink-dim">No pattern passed the false-discovery threshold.</p> : (
            <ul>{r.findings.map((f) => <FindingRow key={f.id} f={f} drafting={draft.isPending && draft.variables === f.id} onDraft={() => draft.mutate(f.id)} />)}</ul>
          )}
          <p className="mt-3 text-xs text-ink-faint">{r.findings[0]?.caveat ?? ''}</p>
        </Section>
      )}
    </div>
  )
}

function ObservationView({ o }: { o: Observation }) {
  const { operator } = useSession()
  const qc = useQueryClient()
  const review = useMutation({
    mutationFn: (status: string) => api.reviewObservation(o.id, status, operator.trim() || 'reviewer'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['observations', o.astronaut_id] }),
  })
  const g = o.grounding as { cited_chunk_ids?: number[]; invalid_citations_removed?: number[]; unverified_numbers?: number[] }
  const refs = o.doc_refs as { chunk_id: number; title: string; organization: string; published_date: string | null; url: string; kind: string; text: string }[]
  return (
    <article className="border-b border-seam pb-6 last:border-0">
      <header className="flex flex-wrap items-center gap-3">
        <h3 className="text-lg font-semibold">{o.title}</h3>
        <span className="text-xs text-signal">{TYPE_LABEL[o.evidence_level] ?? o.evidence_level}</span>
        <span className={`text-xs ${o.status === 'reviewed' ? 'text-sev-normal' : o.status === 'rejected' ? 'text-sev-critical' : 'text-ink-dim'}`}>{titleCase(o.status)}</span>
        {o.is_simulated && <SimulatedMark />}
      </header>
      <dl className="mt-3 grid gap-x-6 gap-y-3 text-sm md:grid-cols-[150px_1fr]">
        <dt className="text-ink-dim">Observation</dt><dd>{o.observation}</dd>
        <dt className="text-ink-dim">Scientific context</dt>
        <dd>
          {refs.length === 0 ? <span className="text-ink-faint">No relevant literature retrieved.</span> : (
            <ul className="space-y-2">
              {refs.map((d) => (
                <li key={d.chunk_id}>
                  <span className="num text-xs text-ink-faint">[{d.chunk_id}]</span>{' '}
                  {d.kind === 'passage' ? <q className="italic">{d.text}</q> : <span>{d.text} <span className="text-xs text-ink-faint">({d.kind === 'finding' ? 'summary, not a quote' : 'stated limitation'})</span></span>}
                  <span className="block text-xs text-ink-dim">
                    <a href={d.url} target="_blank" rel="noreferrer" className="text-signal hover:underline">{d.title}</a>, {d.organization}{d.published_date ? `, ${d.published_date}` : ''}
                    {g.cited_chunk_ids?.includes(d.chunk_id) && <span className="ml-1 text-sev-normal">cited by the hypothesis</span>}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </dd>
        <dt className="text-ink-dim">Hypothesis</dt>
        <dd className="whitespace-pre-wrap">
          {o.hypothesis}
          <span className="mt-1 block text-xs text-ink-faint">
            {o.generated_by === 'llm' ? `Drafted by ${o.model}; citations limited to the excerpts above.` : 'Template text; no language model used.'}
            {g.invalid_citations_removed?.length ? ` Removed citations to excerpts that were not provided: ${g.invalid_citations_removed.join(', ')}.` : ''}
          </span>
          {g.unverified_numbers?.length ? <span className="block text-xs text-sev-warning">Numbers not found in the inputs: {g.unverified_numbers.join(', ')}</span> : null}
        </dd>
        <dt className="text-ink-dim">Confidence</dt><dd className="text-ink-dim">{o.confidence_note}</dd>
      </dl>
      <div className="mt-3 flex gap-2">
        <Button variant="quiet" disabled={review.isPending || o.status === 'reviewed'} onClick={() => review.mutate('reviewed')}>Mark reviewed</Button>
        <Button variant="quiet" disabled={review.isPending || o.status === 'rejected'} onClick={() => review.mutate('rejected')}>Reject</Button>
      </div>
    </article>
  )
}

function Observations({ astronautId }: { astronautId: number }) {
  const q = useQuery({ queryKey: ['observations', astronautId], queryFn: () => api.observations(astronautId) })
  if (q.isError) return <ErrorNote error={q.error} />
  if (!q.data) return <Loading />
  if (!q.data.length) return <Empty title="No research observations yet">Draft one from a pattern in the Patterns tab.</Empty>
  return <div className="flex flex-col gap-6">{q.data.map((o) => <ObservationView key={o.id} o={o} />)}</div>
}

function Spark({ s }: { s: DailySeriesOut }) {
  const ref = s.reference as { median: number; scale: number; days: number[] } | null
  return (
    <figure className="min-w-0">
      <figcaption className="flex justify-between text-sm"><span>{s.label}</span><span className="text-xs text-ink-faint">{s.aggregation}</span></figcaption>
      <div className="h-28">
        <ResponsiveContainer>
          <LineChart data={s.points} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
            <XAxis dataKey="day" type="number" domain={['dataMin', 'dataMax']} tick={{ fill: 'var(--color-ink-faint)', fontSize: 11 }} stroke="var(--color-seam)" />
            <YAxis domain={['auto', 'auto']} width={40} tick={{ fill: 'var(--color-ink-faint)', fontSize: 11 }} stroke="var(--color-seam)" tickFormatter={(v: number) => fmtNum(v, 0)} />
            {ref && <ReferenceArea y1={ref.median - 1.5 * ref.scale} y2={ref.median + 1.5 * ref.scale} fill="var(--color-signal)" fillOpacity={0.08} />}
            {ref && <ReferenceLine y={ref.median} stroke="var(--color-signal)" strokeDasharray="3 3" />}
            <Line dataKey="value" stroke="var(--color-ink)" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            <Tooltip formatter={(v) => [`${fmtNum(Number(v), 2)} ${unitLabel(s.unit)}`, 'value']} labelFormatter={(d) => `Mission day ${d}`}
              contentStyle={{ background: 'var(--color-hull)', border: '1px solid var(--color-seam-strong)', borderRadius: 6 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </figure>
  )
}

function SeriesGrid({ astronautId }: { astronautId: number }) {
  const q = useQuery({ queryKey: ['series', astronautId], queryFn: () => api.series(astronautId) })
  if (q.isError) return <ErrorNote error={q.error} />
  if (!q.data) return <Loading />
  return (
    <div>
      <p className="mb-4 text-sm text-ink-dim">Daily aggregates by mission day. Shaded band: within 1.5 spread units of the mission days 1 to 7 reference.</p>
      <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">{q.data.map((s) => <Spark key={s.key} s={s} />)}</div>
    </div>
  )
}

function Library() {
  const qc = useQueryClient()
  const docs = useQuery({ queryKey: ['documents'], queryFn: api.documents })
  const ingest = useMutation({ mutationFn: api.ingestKnowledge, onSuccess: () => qc.invalidateQueries({ queryKey: ['documents'] }) })
  const [q, setQ] = useState('')
  const search = useMutation({ mutationFn: (text: string) => api.searchKnowledge(text) })
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <p className="mr-auto max-w-[72ch] text-sm text-ink-dim">
          Curated, publicly available sources, mostly NASA Human Research Program pages. Verbatim passages are stored
          only for US government works; other sources keep metadata and AM&apos;s own summary.
        </p>
        <Button variant="quiet" onClick={() => ingest.mutate()} disabled={ingest.isPending}>{ingest.isPending ? 'Indexing…' : 'Re-index sources'}</Button>
      </div>
      {ingest.data && (
        <p role="status" className="text-sm text-ink-dim">
          {ingest.data.documents_total} documents; {ingest.data.loaded} new, {ingest.data.updated} updated, {ingest.data.embedded} passages embedded
          {ingest.data.embed_error ? ` (embeddings unavailable: ${ingest.data.embed_error}; keyword search is used)` : ''}
          {ingest.data.skipped.length ? `; skipped: ${ingest.data.skipped.join('; ')}` : ''}
        </p>
      )}
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (q.trim()) search.mutate(q) }}>
        <label htmlFor="ks" className="sr-only">Search literature</label>
        <input id="ks" className={`${inputCls} flex-1`} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search the literature, e.g. bone loss and exercise" />
        <Button type="submit" disabled={!q.trim() || search.isPending}>Search</Button>
      </form>
      {search.data && (
        <Section title="Results" aside={search.data[0] ? `${search.data[0].method} search` : undefined}>
          {search.data.length === 0 ? <p className="text-sm text-ink-dim">No matching passages.</p> : (
            <ul className="space-y-3 text-sm">
              {search.data.map((h) => (
                <li key={h.chunk_id}>
                  {h.kind === 'passage' ? <q className="italic">{h.text}</q> : <span>{h.text} <span className="text-xs text-ink-faint">({h.kind === 'finding' ? 'summary' : 'limitation'})</span></span>}
                  <span className="block text-xs text-ink-dim"><a className="text-signal hover:underline" href={h.url} target="_blank" rel="noreferrer">{h.title}</a>, {h.organization}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      )}
      <Section title="Sources" aside={`${docs.data?.length ?? 0} documents`}>
        {docs.isLoading ? <Loading /> : !docs.data?.length ? <Empty title="No sources indexed">Re-index sources to load knowledge/sources.</Empty> : (
          <ul className="divide-y divide-seam/60">
            {docs.data.map((d) => (
              <li key={d.id} className="py-2.5 text-sm">
                <a href={d.url} target="_blank" rel="noreferrer" className="font-medium text-ink hover:text-signal">{d.title}</a>
                <span className="block text-xs text-ink-dim">
                  {d.organization}{d.published_date ? `, ${d.published_date}` : ''}{d.authors.length ? `, ${d.authors.slice(0, 3).join(', ')}${d.authors.length > 3 ? ' et al.' : ''}` : ''}. Topics: {d.topics.join(', ')}
                </span>
                <span className="block text-xs text-ink-faint">{d.license_basis}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  )
}

export function SciencePage() {
  const { astronaut } = useSession()
  const [params, setParams] = useSearchParams()
  const tab = TABS.find((t) => t.key === params.get('tab'))?.key ?? 'patterns'
  if (!astronaut) return <NoCrew />
  const go = (k: string) => setParams({ tab: k }, { replace: true })
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end gap-4">
        <h1 className="mr-auto text-2xl font-semibold">Scientific intelligence</h1>
        {astronaut.is_simulated && <SimulatedMark />}
      </div>
      <div role="tablist" aria-label="Science views" className="flex gap-1 border-b border-seam">
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={t.key === tab} onClick={() => go(t.key)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${t.key === tab ? 'border-signal text-ink' : 'border-transparent text-ink-dim hover:text-ink'}`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'patterns' && <Patterns astronautId={astronaut.id} onDrafted={() => go('observations')} />}
      {tab === 'observations' && <Observations astronautId={astronaut.id} />}
      {tab === 'series' && <SeriesGrid astronautId={astronaut.id} />}
      {tab === 'library' && <Library />}
    </div>
  )
}
