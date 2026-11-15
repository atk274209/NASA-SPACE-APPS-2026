import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { SpaceEventsPanel } from '../components/SpaceEvents'
import { SystemMap } from '../components/SystemMap'
import { useState, type FormEvent } from 'react'
import { api } from '../api/client'
import { crewApi } from '../api/crew'
import { Button, ErrorNote, Field, inputCls, KeyValue, Loading, Section, SimulatedMark } from '../components/ui'
import { fmtDateTime } from '../lib/format'
import { useSession } from '../state/session'

/** Today's date in UTC (YYYY-MM-DD): AMC counts mission days from the start date in UTC. */
const utcToday = () => new Date().toISOString().slice(0, 10)

// Inputs with a validation message get a red border while the message is shown.
const fieldCls = `${inputCls} aria-[invalid=true]:border-sev-critical`

function AddCrewMember() {
  const qc = useQueryClient()
  const { astronauts, selectAstronaut } = useSession()
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [attempted, setAttempted] = useState(false)
  const add = useMutation({
    mutationFn: () => crewApi.createAstronaut(code.trim(), name.trim()),
    onSuccess: async (a) => {
      setCode('')
      setName('')
      setAttempted(false)
      await qc.invalidateQueries({ queryKey: ['astronauts'] })
      selectAstronaut(a.id)
    },
  })
  const taken = astronauts.some((a) => a.code.toLowerCase() === code.trim().toLowerCase())
  const codeError = !code.trim() ? (attempted ? 'Enter a crew code, for example CREW-01.' : null)
    : taken ? 'This crew code is already in use.' : null
  const nameError = !name.trim() && attempted ? 'Enter the name to show for this crew member.' : null
  const submit = (e: FormEvent) => {
    e.preventDefault()
    setAttempted(true)
    if (code.trim() && name.trim() && !taken) add.mutate()
  }

  return (
    <form className="flex flex-col gap-3" onSubmit={submit} noValidate>
      <h3 className="text-sm font-semibold text-ink">Add a crew member</h3>
      <div className="grid gap-3 sm:grid-cols-[160px_1fr]">
        <Field label="Crew code">
          <input className={`${fieldCls} num`} maxLength={32} value={code} onChange={(e) => setCode(e.target.value)}
            aria-invalid={codeError !== null} placeholder="CREW-01" />
        </Field>
        <Field label="Display name">
          <input className={fieldCls} maxLength={128} value={name} onChange={(e) => setName(e.target.value)}
            aria-invalid={nameError !== null} />
        </Field>
      </div>
      {(codeError || nameError) && (
        <p role="alert" className="text-xs text-sev-critical">{[codeError, nameError].filter(Boolean).join(' ')}</p>
      )}
      <div>
        <Button type="submit" disabled={add.isPending}>{add.isPending ? 'Adding…' : 'Add crew member'}</Button>
      </div>
      {add.error && <ErrorNote error={add.error} />}
      {add.data && (
        <p role="status" className="text-sm text-sev-normal">
          Added {add.data.display_name} ({add.data.code}). Now viewing this record.
        </p>
      )}
    </form>
  )
}

function StartMission() {
  const qc = useQueryClient()
  const { astronauts, astronaut, operator } = useSession()
  const environments = useQuery({ queryKey: ['mission-environments'], queryFn: crewApi.environments, staleTime: Infinity })
  const [crewId, setCrewId] = useState<number | null>(null)
  const [name, setName] = useState('')
  const [start, setStart] = useState(utcToday)
  const [plannedRaw, setPlannedRaw] = useState('')
  const [environment, setEnvironment] = useState('')
  const [attempted, setAttempted] = useState(false)

  const targetId = crewId ?? astronaut?.id ?? null
  const target = astronauts.find((a) => a.id === targetId)
  const planned = plannedRaw.trim() === '' ? null : Number(plannedRaw)
  const errors = [
    !name.trim() && 'Enter a mission name.',
    !start ? 'Choose the start date.' : start > utcToday() && 'The start date cannot be in the future (UTC).',
    planned !== null && !(Number.isInteger(planned) && planned >= 1) && 'Planned days must be a whole number of 1 or more.',
    !environment && 'Choose an environment, or Not specified.',
  ].filter((x): x is string => typeof x === 'string')

  const create = useMutation({
    mutationFn: () =>
      crewApi.createMission({
        astronaut_id: targetId as number,
        name: name.trim(),
        start_date: start,
        planned_days: planned,
        environment,
        actor: operator.trim() || 'operator',
      }),
    onSuccess: () => {
      setName('')
      setPlannedRaw('')
      setAttempted(false)
      qc.invalidateQueries()
    },
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    setAttempted(true)
    if (targetId !== null && errors.length === 0) create.mutate()
  }
  const envHelp = environments.data?.find((x) => x.code === environment)?.description

  return (
    <form className="flex flex-col gap-3" onSubmit={submit} noValidate>
      <h3 className="text-sm font-semibold text-ink">Start a mission</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Crew member">
          <select className={inputCls} value={targetId ?? ''} onChange={(e) => setCrewId(Number(e.target.value))}>
            {astronauts.map((a) => <option key={a.id} value={a.id}>{a.display_name} ({a.code})</option>)}
          </select>
        </Field>
        <Field label="Mission name">
          <input className={inputCls} maxLength={128} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Start date (UTC)">
          <input className={`${inputCls} num`} type="date" max={utcToday()} value={start}
            onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label="Planned days (optional)">
          <input className={`${inputCls} num`} inputMode="numeric" value={plannedRaw}
            onChange={(e) => setPlannedRaw(e.target.value)} />
        </Field>
      </div>
      <Field label="Environment">
        <select className={inputCls} value={environment} onChange={(e) => setEnvironment(e.target.value)}>
          <option value="" disabled>Choose an environment</option>
          {environments.data?.map((x) => <option key={x.code} value={x.code}>{x.label}</option>)}
        </select>
      </Field>
      {envHelp && <p className="text-xs text-ink-dim">{envHelp}</p>}
      {environments.error && <ErrorNote error={environments.error} />}
      {attempted && errors.length > 0 && <p role="alert" className="text-xs text-sev-critical">{errors.join(' ')}</p>}
      {target?.is_simulated && (
        <p className="text-xs text-ink-dim">
          {target.code} is a simulated record, so this mission is marked simulated too.
        </p>
      )}
      <p className="text-xs text-ink-faint">
        The newest mission becomes the current one: mission day 1 is its start date. Recorded in the audit trail as{' '}
        {operator.trim() || 'operator'}.
      </p>
      <div>
        <Button type="submit" disabled={create.isPending || targetId === null}>
          {create.isPending ? 'Creating…' : 'Create mission'}
        </Button>
      </div>
      {create.error && <ErrorNote error={create.error} />}
      {create.data && (
        <p role="status" className="text-sm text-sev-normal">
          Created {create.data.name} ({create.data.environment_label}). It is now the current mission for{' '}
          {astronauts.find((a) => a.id === create.data.astronaut_id)?.code ?? 'this crew member'}.
        </p>
      )}
    </form>
  )
}

function MissionList() {
  const { astronaut } = useSession()
  const missions = useQuery({
    queryKey: ['crew-missions', astronaut?.id],
    queryFn: () => crewApi.missions(astronaut!.id),
    enabled: !!astronaut,
  })
  if (!astronaut) return null
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-ink">Missions for {astronaut.display_name}</h3>
      {missions.isError ? <ErrorNote error={missions.error} /> : !missions.data ? <Loading /> : missions.data.length === 0 ? (
        <p className="text-sm text-ink-dim">No missions yet. Mission days and the journal start once a mission exists.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-sm">
            <thead className="text-left text-ink-dim">
              <tr>
                <th className="pb-1 font-normal">Mission</th>
                <th className="pb-1 font-normal">Environment</th>
                <th className="pb-1 font-normal">Start (UTC)</th>
                <th className="pb-1 text-right font-normal">Planned days</th>
              </tr>
            </thead>
            <tbody>
              {missions.data.map((m) => (
                <tr key={m.id} className="border-t border-seam/60">
                  <td className="py-1.5">
                    <span className="inline-flex flex-wrap items-center gap-2">
                      {m.name}
                      {m.is_current && <span className="text-xs text-signal">Current</span>}
                      {m.is_simulated && <SimulatedMark />}
                    </span>
                  </td>
                  <td className="py-1.5 text-ink-dim">{m.environment_label}</td>
                  <td className="num py-1.5 text-ink-dim">{m.start_date}</td>
                  <td className="num py-1.5 text-right">{m.planned_days ?? '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function CrewAndMissions() {
  const { astronauts } = useSession()
  return (
    <Section title="Crew and missions">
      <div className="flex flex-col gap-6">
        <div className="grid gap-8 lg:grid-cols-2">
          <AddCrewMember />
          {astronauts.length ? <StartMission /> : (
            <p className="text-sm text-ink-dim">Add a crew member first, then start a mission for them.</p>
          )}
        </div>
        <MissionList />
      </div>
    </Section>
  )
}

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
      <SystemMap />
      <SpaceEventsPanel />
      <div className="grid gap-8 lg:grid-cols-2">
        <Section title="Service details">
          {status.isError ? <ErrorNote error={status.error} /> : !status.data ? <Loading /> : (
            <details className="group">
              <summary className="cursor-pointer text-sm text-ink-dim hover:text-ink">Show addresses, models and versions</summary>
              <div className="mt-3">
                <KeyValue
                  items={[
                    ['Database', status.data.database === 'ok' ? <span className="text-sev-normal">Online</span> : status.data.database],
                    ['Data folder', <span className="num break-all text-sm">{status.data.data_dir}</span>],
                    ['Language model service', o?.reachable ? <span className="text-sev-normal">Reachable at {o.url}</span> : <span className="text-ink-dim">Not running. Monitoring and alerts do not need it.</span>],
                    ['Baseline policy', <span className="num">{status.data.baseline_policy_version}</span>],
                  ]}
                />
              </div>
            </details>
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
      <CrewAndMissions />
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
