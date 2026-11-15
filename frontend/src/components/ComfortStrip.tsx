// Module P, astronaut side: their quarters (temperature, lighting) and the shared cabin pressure.
// AMC tells them what it changed; one tap undoes it; manual control always wins.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { cabinApi, VAR_LABEL, type CabinAction, type Control } from '../api/cabin'
import { unitLabel } from '../lib/format'
import { ErrorNote } from './ui'

function useCabinMutation<T>(astronautId: number, fn: (x: T) => Promise<unknown>) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: fn, onSuccess: () => qc.invalidateQueries({ queryKey: ['cabin', astronautId] }) })
}

function ControlTile({ c, astronautId }: { c: Control; astronautId: number }) {
  const set = useCabinMutation(astronautId, (v: number) => cabinApi.set(c.id, v))
  const resume = useCabinMutation(astronautId, () => cabinApi.mode(c.id, 'AUTONOMOUS', 'resumed by the astronaut'))
  const own = c.astronaut_id === astronautId
  return (
    <div className="grid gap-1 rounded-md border border-seam bg-deck px-3 py-2 text-sm">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-ink-dim">{VAR_LABEL[c.variable]}</span>
        <span className="text-xs text-ink-faint" title={c.mode_reason}>{c.mode === 'AUTONOMOUS' ? 'AMC control' : c.mode.toLowerCase()}</span>
      </div>
      <span className="num text-lg text-ink">{c.reading ?? c.setpoint} {unitLabel(c.unit)}</span>
      {own ? (
        <div className="flex flex-wrap items-center gap-1">
          <button aria-label={`Lower ${c.variable}`} className="size-7 rounded border border-seam-strong hover:border-signal disabled:opacity-40"
            disabled={set.isPending || c.setpoint - c.step < c.min} onClick={() => set.mutate(+(c.setpoint - c.step).toFixed(2))}>−</button>
          <button aria-label={`Raise ${c.variable}`} className="size-7 rounded border border-seam-strong hover:border-signal disabled:opacity-40"
            disabled={set.isPending || c.setpoint + c.step > c.max} onClick={() => set.mutate(+(c.setpoint + c.step).toFixed(2))}>+</button>
          {c.mode === 'MANUAL' && (
            <button className="ml-auto text-xs text-signal hover:underline" onClick={() => resume.mutate(undefined)}>Let AMC adjust again</button>
          )}
        </div>
      ) : (
        <span className="text-xs text-ink-faint">Shared and safety-critical: only the medical officer changes it.</span>
      )}
      {(set.error || resume.error) && <ErrorNote error={set.error ?? resume.error} />}
    </div>
  )
}

function LastChange({ a, astronautId }: { a: CabinAction; astronautId: number }) {
  const undo = useCabinMutation(astronautId, () => cabinApi.revert(a.id))
  const fb = useCabinMutation(astronautId, (helped: boolean) => cabinApi.feedback(a.id, helped))
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-ink-dim">
      <span>
        AMC changed {VAR_LABEL[a.variable]?.toLowerCase()} {a.before} → {a.after}
        {a.signal_quote && <> because you said “{a.signal_quote}”</>}.
      </span>
      {!a.reverted && <button className="text-signal hover:underline" onClick={() => undo.mutate(undefined)}>Undo</button>}
      {!a.feedback && !a.reverted && (
        <>
          <span>Did it help?</span>
          <button className="text-signal hover:underline" onClick={() => fb.mutate(true)}>Yes</button>
          <button className="text-signal hover:underline" onClick={() => fb.mutate(false)}>No</button>
        </>
      )}
      {a.feedback && <span className="text-ink-faint">({a.feedback === 'helped' ? 'helped' : 'did not help'})</span>}
    </div>
  )
}

export function ComfortStrip({ astronautId }: { astronautId: number }) {
  const q = useQuery({ queryKey: ['cabin', astronautId], queryFn: () => cabinApi.state(astronautId), refetchInterval: 10_000 })
  if (q.error) return <ErrorNote error={q.error} />
  if (!q.data) return null
  const lastAuto = q.data.actions.find((a) => a.kind === 'AUTO')
  return (
    <section className="grid gap-2">
      <h2 className="text-sm font-semibold text-ink">Your quarters <span className="font-normal text-ink-faint">· SIMULATED cabin · {q.data.label}</span></h2>
      <div className="grid grid-cols-3 gap-2">
        {q.data.controls.map((c) => <ControlTile key={c.id} c={c} astronautId={astronautId} />)}
      </div>
      {lastAuto && <LastChange a={lastAuto} astronautId={astronautId} />}
    </section>
  )
}
