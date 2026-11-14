import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import { Button, ErrorNote, Field, inputCls, KeyValue, Loading, Section } from '../components/ui'
import { fmtDateTime } from '../lib/format'
import { useSession } from '../state/session'

function SimulationRunner() {
  const qc = useQueryClient()
  const { selectAstronaut, astronauts } = useSession()
  const scenarios = useQuery({ queryKey: ['scenarios'], queryFn: api.scenarios })
  const [scenario, setScenario] = useState('acute_rest_hr')
  const [code, setCode] = useState('')
  const [seed, setSeed] = useState(42)
  const suggested = `SIM-${String(astronauts.length + 1).padStart(3, '0')}`
  const run = useMutation({
    mutationFn: () =>
      api.runSimulation({
        scenario,
        seed,
        astronaut_code: code.trim() || suggested,
        display_name: `Simulated astronaut ${code.trim() || suggested}`,
      }),
    onSuccess: async (r) => {
      setCode('')
      await qc.invalidateQueries({ queryKey: ['astronauts'] })
      qc.invalidateQueries()
      selectAstronaut(r.astronaut.id)
    },
  })
  const desc = scenarios.data?.find((s) => s.name === scenario)?.description

  return (
    <Section title="Simulated missions">
      <p className="mb-4 max-w-[72ch] text-sm text-ink-dim">
        Creates a new astronaut record filled with generated readings. Every record is marked as simulated and is
        never mixed with real measurements. Values follow simple demonstration distributions, not physiological norms.
      </p>
      <form className="grid gap-3 sm:grid-cols-[1fr_160px_100px_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); run.mutate() }}>
        <Field label="Scenario">
          <select className={inputCls} value={scenario} onChange={(e) => setScenario(e.target.value)}>
            {scenarios.data?.map((s) => <option key={s.name} value={s.name}>{s.name.replaceAll('_', ' ')} ({s.days} days)</option>)}
          </select>
        </Field>
        <Field label="Crew code">
          <input className={inputCls} value={code} onChange={(e) => setCode(e.target.value)} placeholder={suggested} />
        </Field>
        <Field label="Seed">
          <input className={`${inputCls} num`} type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
        </Field>
        <Button type="submit" disabled={run.isPending || !scenarios.data}>
          {run.isPending ? 'Generating…' : 'Generate mission'}
        </Button>
      </form>
      {desc && <p className="mt-2 text-sm text-ink-dim">{desc}</p>}
      {run.error && <div className="mt-3"><ErrorNote error={run.error} /></div>}
      {run.data && (
        <p role="status" className="mt-3 text-sm text-sev-normal">
          Generated {run.data.measurements_ingested.toLocaleString()} readings for {run.data.astronaut.code}:{' '}
          {run.data.baseline_profiles} baseline versions, {run.data.alerts_open} open alerts. Now viewing this record.
        </p>
      )}
    </Section>
  )
}

export function SystemPage() {
  const status = useQuery({ queryKey: ['status'], queryFn: api.status, refetchInterval: 20_000, retry: 0 })
  const thresholds = useQuery({ queryKey: ['thresholds'], queryFn: api.thresholds })
  const audit = useQuery({ queryKey: ['audit'], queryFn: () => api.audit(40), refetchInterval: 20_000 })
  const o = status.data?.ollama as
    | { url: string; reachable: boolean; models: string[]; configured_chat_model: string; configured_embed_model: string }
    | undefined

  return (
    <div className="flex flex-col gap-8">
      <h1 className="text-2xl font-semibold">System</h1>
      <div className="grid gap-8 lg:grid-cols-2">
        <Section title="Local services">
          {status.isError ? <ErrorNote error={status.error} /> : !status.data ? <Loading /> : (
            <KeyValue
              items={[
                ['Database', status.data.database === 'ok' ? <span className="text-sev-normal">Online</span> : status.data.database],
                ['Data folder', <span className="num break-all text-sm">{status.data.data_dir}</span>],
                ['Language model service', o?.reachable ? <span className="text-sev-normal">Reachable at {o.url}</span> : <span className="text-ink-dim">Not running. Monitoring and alerts do not need it.</span>],
                ['Installed models', o?.reachable ? (o.models.length ? o.models.join(', ') : <span className="text-sev-watch">None installed yet</span>) : '–'],
                ['Configured chat model', <span className="num">{o?.configured_chat_model}</span>],
                ['Baseline policy', <span className="num">{status.data.baseline_policy_version}</span>],
              ]}
            />
          )}
        </Section>
        <Section title="Alert thresholds" aside={thresholds.data && <span className="text-sev-watch">{thresholds.data.label}</span>}>
          {!thresholds.data ? <Loading /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-ink-dim">
                  <tr><th className="pb-1 font-normal">Indicator</th><th className="pb-1 font-normal">Direction</th><th className="pb-1 text-right font-normal">Watch</th><th className="pb-1 text-right font-normal">Warning</th><th className="pb-1 text-right font-normal">Critical</th></tr>
                </thead>
                <tbody>
                  {thresholds.data.rules.map((r) => (
                    <tr key={r.metric_code} className="border-t border-seam/60">
                      <td className="py-1.5">{r.metric_code.replaceAll('_', ' ')}</td>
                      <td className="py-1.5 text-ink-dim">{r.direction}</td>
                      <td className="num py-1.5 text-right">{r.bands.WATCH}σ</td>
                      <td className="num py-1.5 text-right">{r.bands.WARNING}σ</td>
                      <td className="num py-1.5 text-right">{r.bands.CRITICAL}σ</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-xs text-ink-faint">
                Deviation from the astronaut&apos;s own median, in units of their personal spread. Edited in
                config/thresholds.demo.yaml; replace with medically reviewed values before operational use.
              </p>
            </div>
          )}
        </Section>
      </div>
      <SimulationRunner />
      <Section title="Audit trail" aside="Latest 40 entries">
        {audit.isError ? <ErrorNote error={audit.error} /> : !audit.data ? <Loading /> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="text-left text-ink-dim">
                <tr><th className="pb-1 font-normal">Time</th><th className="pb-1 font-normal">Actor</th><th className="pb-1 font-normal">Action</th><th className="pb-1 font-normal">Record</th></tr>
              </thead>
              <tbody>
                {audit.data.map((e) => (
                  <tr key={e.id} className="border-t border-seam/60">
                    <td className="num py-1.5 text-ink-dim">{fmtDateTime(e.created_at)}</td>
                    <td className="py-1.5">{e.actor}</td>
                    <td className="num py-1.5">{e.action}</td>
                    <td className="num py-1.5 text-ink-dim">{e.entity} {e.entity_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  )
}
