// Module N: why an astronaut's DEMO risk level is what it is (deterministic factors, rule ids, records).
import { useQuery } from '@tanstack/react-query'
import { request } from '../api/client'
import type { FlagLevel } from '../api/doctor'
import { LevelDot } from './Feed'
import { ErrorNote } from './ui'

interface Factor { kind: string; level: FlagLevel; text: string; rule_ids: string[]; record_refs: { type: string; id: number }[] }
interface Explain { level: FlagLevel; label: string; factors: Factor[] }

export function ExplainBox({ astronautId }: { astronautId: number }) {
  const q = useQuery({
    queryKey: ['explain', astronautId],
    queryFn: () => request<Explain>('GET', '/explain', undefined, { astronaut_id: astronautId }),
    refetchInterval: 10_000,
  })
  if (q.error) return <ErrorNote error={q.error} />
  if (!q.data) return null
  return (
    <section className="rounded-md border border-seam bg-hull p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-semibold text-ink">Why this level</h2>
        <LevelDot level={q.data.level} />
        <span className="text-xs text-ink-faint">{q.data.label}</span>
      </div>
      {q.data.factors.length === 0 ? (
        <p className="mt-2 text-sm text-ink-dim">No open alert, no flagged report in the last 24 hours, no trend away from the personal baseline.</p>
      ) : (
        <ul className="mt-2 grid gap-1.5 text-sm">
          {q.data.factors.map((f, i) => (
            <li key={i} className="flex gap-2">
              <span className="pt-0.5"><LevelDot level={f.level} /></span>
              <span className="text-ink">
                {f.text}
                <span className="num block text-xs text-ink-faint">
                  rules {f.rule_ids.join(', ') || '—'} · records {f.record_refs.map((r) => `${r.type} #${r.id}`).join(', ')}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
