import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import {
  adminApi,
  REPORT_SECTIONS,
  type Backup,
  type Diagnostics,
  type OutboxPackage,
  type ReportSection,
  type Setting,
} from '../api/admin'
import { api, ApiError, type AuditEntry } from '../api/client'
import { Button, Empty, ErrorNote, Field, inputCls, KeyValue, Loading, Section, SimulatedMark } from '../components/ui'
import { fmtDateTime } from '../lib/format'
import { useSession } from '../state/session'

const MIN_PASSPHRASE = 12

const SECTION_LABEL: Record<ReportSection, string> = {
  alerts: 'Alerts',
  journal: 'Mission journal',
  observations: 'Research observations',
  symptoms: 'Symptom reports',
  checkins: 'Check-ins',
  missions: 'Daily missions',
}

const SECTION_HINT: Record<ReportSection, string> = {
  alerts: 'Alerts open during the period, with their supporting readings and reviews',
  journal: 'Stored journal entries for those days, including any drafted narrative',
  observations: 'Research observations and the statistical findings behind them',
  symptoms: 'Symptom reports made or still active during the period',
  checkins: 'Self-rated check-ins and their notes',
  missions: 'Daily mission tasks and whether they were done',
}

const STORE_LABEL: Record<string, string> = {
  database: 'Database',
  backups: 'Encrypted backups',
  outbox: 'Outbox',
  pre_restore: 'Pre-restore copies',
}

const AUDIT_LABEL: Record<string, string> = {
  'setting.changed': 'Setting changed',
  'backup.created': 'Backup created',
  'backup.verified': 'Backup verified',
  'backup.pruned': 'Backup removed by retention',
  'backup.restored': 'Database restored',
  'outbox.prepared': 'Ground report prepared',
  'outbox.downloaded': 'Package file downloaded',
  'outbox.exported': 'Package marked as exported',
}

function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '–'
  const units = ['bytes', 'KiB', 'MiB', 'GiB', 'TiB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return i === 0 ? `${v} bytes` : `${v.toFixed(1)} ${units[i]}`
}

/** Validation errors arrive as a JSON list; turn them into readable lines. */
function readable(error: unknown): unknown {
  if (error instanceof ApiError && error.message.startsWith('[')) {
    try {
      const items = JSON.parse(error.message) as { loc?: (string | number)[]; msg?: string }[]
      const text = items.map((i) => `${(i.loc ?? []).filter((x) => x !== 'body').join('.')}: ${i.msg ?? 'invalid'}`)
      return new Error(text.join('; '))
    } catch {
      /* not a validation list; show as is */
    }
  }
  return error
}

function Problem({ error }: { error: unknown }) {
  return error ? <ErrorNote error={readable(error)} /> : null
}

function useActor(): string {
  return useSession().operator.trim()
}

function NeedsName() {
  return <span className="text-xs text-ink-faint">Enter your name at the top of the page first.</span>
}

function useInvalidateAdmin() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: ['admin'] })
}

function Subheading({ children }: { children: ReactNode }) {
  return <h3 className="mb-2 text-sm font-medium text-ink">{children}</h3>
}

// ------------------------------------------------------------------ local AI model
function ModelPicker({ setting, label, ollama }: { setting: Setting; label: string; ollama?: Diagnostics['ollama'] }) {
  const actor = useActor()
  const invalidate = useInvalidateAdmin()
  const current = typeof setting.value === 'string' ? setting.value : ''
  const [choice, setChoice] = useState(current)
  const save = useMutation({
    mutationFn: () => adminApi.updateSetting(setting.key, choice || null, actor),
    onSuccess: invalidate,
  })
  const reachable = !!ollama?.reachable
  const options = [...new Set([...(ollama?.installed_models ?? []), ...(current ? [current] : [])])].sort()
  const installed = setting.key === 'chat_model' ? ollama?.chat_model_installed : ollama?.embed_model_installed
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end gap-3">
        <Field label={label}>
          <select className={`${inputCls} min-w-[260px]`} value={choice} onChange={(e) => setChoice(e.target.value)}>
            <option value="">Deployment default ({setting.deployment_default})</option>
            {options.map((m) => (
              <option key={m} value={m} disabled={!reachable && m !== current}>{m}</option>
            ))}
          </select>
        </Field>
        <Button
          onClick={() => save.mutate()}
          disabled={!actor || save.isPending || choice === current || (choice !== '' && !reachable)}
        >
          {save.isPending ? 'Checking…' : 'Use this model'}
        </Button>
        {!actor && <NeedsName />}
      </div>
      <p className="text-sm text-ink-dim">
        In use: <span className="num text-ink">{String(setting.effective)}</span>
        {installed === true && <span className="text-sev-normal">, installed</span>}
        {installed === false && <span className="text-sev-warning">, not installed: features that need it use their offline fallback</span>}
        {installed == null && <span className="text-ink-faint">, installation unknown while the model service is offline</span>}
        {setting.updated_by && <span className="text-ink-faint">. Last changed by {setting.updated_by}</span>}
      </p>
      <p className="max-w-[80ch] text-xs text-ink-faint">{setting.description}</p>
      <Problem error={save.error} />
    </div>
  )
}

function BoolSetting({ setting, label }: { setting: Setting; label: string }) {
  const actor = useActor()
  const invalidate = useInvalidateAdmin()
  const save = useMutation({
    mutationFn: (value: boolean) => adminApi.updateSetting(setting.key, value, actor),
    onSuccess: invalidate,
  })
  const checked = save.isPending ? !!save.variables : setting.value === true
  return (
    <div className="flex flex-col gap-1">
      <label className="inline-flex items-center gap-2 text-sm text-ink">
        <input
          type="checkbox"
          className="size-4 accent-[var(--color-signal)]"
          checked={checked}
          disabled={!actor || save.isPending}
          onChange={(e) => save.mutate(e.target.checked)}
        />
        {label}
        {!actor && <NeedsName />}
      </label>
      <p className="max-w-[80ch] pl-6 text-xs text-ink-faint">
        {setting.description}
        {setting.updated_by && ` Last changed by ${setting.updated_by}.`}
      </p>
      <Problem error={save.error} />
    </div>
  )
}

function Reindex({ embeddings }: { embeddings?: Diagnostics['embeddings'] }) {
  const qc = useQueryClient()
  const ingest = useMutation({
    mutationFn: api.ingestKnowledge,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin'] })
      qc.invalidateQueries({ queryKey: ['documents'] })
    },
  })
  return (
    <div className="flex flex-col gap-2">
      {embeddings && (
        <p className="text-sm text-ink-dim">
          {embeddings.chunks_total === 0
            ? 'The knowledge base has not been indexed yet.'
            : `${embeddings.chunks_current_model} of ${embeddings.chunks_total} knowledge passages carry an embedding from this model. Search method: ${embeddings.search_method}.`}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="quiet" onClick={() => ingest.mutate()} disabled={ingest.isPending}>
          {ingest.isPending ? 'Re-indexing…' : 'Re-index knowledge base'}
        </Button>
        {ingest.data && (
          <span role="status" className="text-sm text-ink-dim">
            {ingest.data.embedded} passages embedded
            {ingest.data.embed_error ? `; embeddings unavailable (${ingest.data.embed_error}), keyword search stays in use` : ''}.
          </span>
        )}
      </div>
      <Problem error={ingest.error} />
    </div>
  )
}

function ModelSection({ settings, diag }: { settings: UseQueryResult<Setting[]>; diag: UseQueryResult<Diagnostics> }) {
  const o = diag.data?.ollama
  const byKey = Object.fromEntries((settings.data ?? []).map((s) => [s.key, s])) as Record<string, Setting | undefined>
  const chat = byKey.chat_model
  const embed = byKey.embed_model
  const detection = byKey.symptom_llm_detection
  return (
    <Section
      title="Local AI model"
      aside={o && (o.reachable ? <span className="text-sev-normal">Model service reachable</span> : <span>Model service offline</span>)}
    >
      <p className="mb-5 max-w-[80ch] text-sm text-ink-dim">
        AMC uses a language model only through the Ollama service at <span className="num text-ink">{o?.url ?? '…'}</span>.
        Monitoring, alerts, backups and ground reports never depend on it.
        {o && !o.reachable && ' Ollama is not reachable right now: you can return to the deployment default, but a different model can only be chosen while Ollama runs, so AMC can confirm it is installed.'}
      </p>
      {settings.isError ? <ErrorNote error={settings.error} /> : !chat || !embed || !detection ? <Loading /> : (
        <div className="flex flex-col gap-8">
          <div className="grid gap-8 lg:grid-cols-2">
            <ModelPicker key={`chat-${String(chat.value)}`} setting={chat} label="Chat model" ollama={o} />
            <div className="flex flex-col gap-4">
              <ModelPicker key={`embed-${String(embed.value)}`} setting={embed} label="Embedding model" ollama={o} />
              <Reindex embeddings={diag.data?.embeddings} />
            </div>
          </div>
          <BoolSetting setting={detection} label="Also ask the local model to notice possible symptoms in chat" />
        </div>
      )}
    </Section>
  )
}

// ------------------------------------------------------------------ data management
function PassphraseFields({
  pass,
  confirm,
  onPass,
  onConfirm,
}: {
  pass: string
  confirm: string
  onPass: (v: string) => void
  onConfirm: (v: string) => void
}) {
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={`Passphrase (at least ${MIN_PASSPHRASE} characters)`}>
          <input type="password" autoComplete="new-password" className={inputCls} value={pass}
            onChange={(e) => onPass(e.target.value)} aria-invalid={pass.length > 0 && pass.length < MIN_PASSPHRASE} />
        </Field>
        <Field label="Repeat passphrase">
          <input type="password" autoComplete="new-password" className={inputCls} value={confirm}
            onChange={(e) => onConfirm(e.target.value)} aria-invalid={confirm.length > 0 && confirm !== pass} />
        </Field>
      </div>
      {pass.length > 0 && pass.length < MIN_PASSPHRASE && (
        <p className="text-xs text-sev-watch">The passphrase needs at least {MIN_PASSPHRASE} characters.</p>
      )}
      {confirm.length > 0 && confirm !== pass && <p className="text-xs text-sev-watch">The two passphrases do not match.</p>}
    </>
  )
}

const passphraseReady = (pass: string, confirm: string) => pass.length >= MIN_PASSPHRASE && pass === confirm

function BackupForm() {
  const actor = useActor()
  const invalidate = useInvalidateAdmin()
  const [pass, setPass] = useState('')
  const [confirm, setConfirm] = useState('')
  const [reason, setReason] = useState('')
  const create = useMutation({
    mutationFn: () => adminApi.createBackup({ passphrase: pass, actor, reason: reason.trim() }),
    onSuccess: () => {
      setPass('')
      setConfirm('')
      setReason('')
      invalidate()
    },
  })
  const ready = !!actor && passphraseReady(pass, confirm) && reason.trim().length >= 3
  return (
    <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (ready) create.mutate() }}>
      <PassphraseFields pass={pass} confirm={confirm} onPass={setPass} onConfirm={setConfirm} />
      <Field label="Reason">
        <input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. Weekly backup before a software update" />
      </Field>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={!ready || create.isPending}>
          {create.isPending ? 'Encrypting…' : 'Create encrypted backup'}
        </Button>
        {!actor && <NeedsName />}
      </div>
      <p className="text-xs text-ink-faint">
        AMC never stores the passphrase and cannot recover it. Without it, the backup cannot be opened.
      </p>
      <Problem error={create.error} />
      {create.data && (
        <p role="status" className="text-sm text-sev-normal">
          Backup {create.data.id} written: <span className="num">{create.data.file_name}</span>, {fmtBytes(create.data.size_bytes)}.
          {create.data.pruned_backup_ids.length > 0 &&
            ` Older backups ${create.data.pruned_backup_ids.join(', ')} were removed under the retention setting.`}
        </p>
      )}
    </form>
  )
}

function RetentionSetting({ setting }: { setting: Setting }) {
  const actor = useActor()
  const invalidate = useInvalidateAdmin()
  const [value, setValue] = useState(String(setting.value ?? ''))
  const n = Number(value)
  const valid = value !== '' && Number.isInteger(n) && n >= (setting.min_value ?? 1) && n <= (setting.max_value ?? 1000)
  const save = useMutation({ mutationFn: () => adminApi.updateSetting(setting.key, n, actor), onSuccess: invalidate })
  return (
    <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); if (valid) save.mutate() }}>
      <Field label="Backups to keep">
        <input type="number" className={`${inputCls} num w-28`} min={setting.min_value ?? 1} max={setting.max_value ?? 1000}
          step={1} value={value} onChange={(e) => setValue(e.target.value)} />
      </Field>
      <Button type="submit" variant="quiet" disabled={!actor || !valid || n === setting.value || save.isPending}>
        Save
      </Button>
      <p className="basis-full max-w-[80ch] text-xs text-ink-faint">{setting.description}</p>
      <div className="basis-full"><Problem error={save.error} /></div>
    </form>
  )
}

function VerifyForm({ backup }: { backup: Backup }) {
  const actor = useActor()
  const qc = useQueryClient()
  const [pass, setPass] = useState('')
  const verify = useMutation({
    mutationFn: () => adminApi.verifyBackup(backup.id, pass, actor),
    onSuccess: () => {
      setPass('')
      qc.invalidateQueries({ queryKey: ['admin', 'audit'] })
    },
  })
  const r = verify.data
  return (
    <form className="flex flex-wrap items-end gap-3 py-2" onSubmit={(e) => { e.preventDefault(); if (actor && pass) verify.mutate() }}>
      <Field label={`Passphrase for backup ${backup.id}`}>
        <input type="password" autoComplete="off" className={inputCls} value={pass} onChange={(e) => setPass(e.target.value)} />
      </Field>
      <Button type="submit" variant="quiet" disabled={!actor || !pass || verify.isPending}>
        {verify.isPending ? 'Decrypting…' : 'Verify'}
      </Button>
      {!actor && <NeedsName />}
      {r && (
        <p role="status" className={`basis-full text-sm ${r.ok ? 'text-sev-normal' : 'text-sev-warning'}`}>
          {r.message}
          {r.ok && ` Schema revision ${r.alembic_revision}, ${r.tables} tables.`}
          {!r.ok && r.decrypted && r.integrity.length > 0 && ` Integrity check: ${r.integrity.slice(0, 3).join('; ')}.`}
        </p>
      )}
      <div className="basis-full"><Problem error={verify.error} /></div>
    </form>
  )
}

function BackupList() {
  const backups = useQuery({ queryKey: ['admin', 'backups'], queryFn: adminApi.backups })
  const [open, setOpen] = useState<number | null>(null)
  if (backups.isError) return <ErrorNote error={backups.error} />
  if (!backups.data) return <Loading />
  if (backups.data.length === 0) return <Empty title="No backups yet">Create the first encrypted backup above.</Empty>
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-sm">
        <thead className="text-left text-ink-dim">
          <tr>
            <th className="pb-1 font-normal">Created</th>
            <th className="pb-1 font-normal">File</th>
            <th className="pb-1 text-right font-normal">Size</th>
            <th className="pb-1 pl-4 font-normal">Status</th>
            <th className="pb-1 font-normal">By</th>
            <th className="pb-1 font-normal">Reason</th>
            <th className="pb-1 font-normal"><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {backups.data.map((b) => (
            <BackupRows key={b.id} b={b} open={open === b.id} onToggle={() => setOpen(open === b.id ? null : b.id)} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function BackupRows({ b, open, onToggle }: { b: Backup; open: boolean; onToggle: () => void }) {
  const usable = b.status === 'stored' && b.file_present
  return (
    <>
      <tr className="border-t border-seam/60 align-top">
        <td className="num py-1.5 text-ink-dim">{fmtDateTime(b.created_at)}</td>
        <td className="num py-1.5 break-all" title={b.sha256 ? `SHA-256 ${b.sha256}` : undefined}>{b.file_name ?? '–'}</td>
        <td className="num py-1.5 text-right">{fmtBytes(b.size_bytes)}</td>
        <td className="py-1.5 pl-4">
          {b.status === 'pruned' ? <span className="text-ink-faint">Removed by retention</span>
            : b.file_present ? 'Stored' : <span className="text-sev-warning">File missing</span>}
        </td>
        <td className="py-1.5">{b.authorized_by}</td>
        <td className="py-1.5 text-ink-dim">{b.reason}</td>
        <td className="py-1.5 text-right">
          {usable && <Button variant="quiet" onClick={onToggle} aria-expanded={open}>{open ? 'Close' : 'Verify'}</Button>}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={7} className="pb-2">
            <VerifyForm backup={b} />
          </td>
        </tr>
      )}
    </>
  )
}

function DataSection({ settings }: { settings: UseQueryResult<Setting[]> }) {
  const retention = settings.data?.find((s) => s.key === 'backup_retention')
  return (
    <Section title="Data management">
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div>
          <Subheading>Create an encrypted backup</Subheading>
          <p className="mb-3 max-w-[70ch] text-sm text-ink-dim">
            A consistent copy of the whole database, encrypted with AES-256-GCM using a key derived from your passphrase.
            It is written to the backups folder in the data folder.
          </p>
          <BackupForm />
        </div>
        <div className="flex flex-col gap-6">
          <div>
            <Subheading>Retention</Subheading>
            {retention ? <RetentionSetting key={String(retention.value)} setting={retention} /> : <Loading />}
          </div>
          <div>
            <Subheading>Restoring a backup</Subheading>
            <p className="text-sm text-ink-dim">
              Restore runs only from the command line, so a browser can never replace the database. Stop the AMC backend,
              then run from the backend folder:
            </p>
            <pre className="num mt-2 overflow-x-auto rounded-md border border-seam bg-deck px-3 py-2 text-xs text-ink">
              uv run python ../scripts/restore_backup.py &lt;backup file&gt; --actor "Your name" --reason "Why" --confirm
            </pre>
            <p className="mt-2 text-xs text-ink-faint">
              It asks for the passphrase, checks the backup, and saves a copy of the current database in the
              pre-restore folder before replacing it.
            </p>
          </div>
        </div>
      </div>
      <div className="mt-8">
        <Subheading>Backups</Subheading>
        <BackupList />
      </div>
    </Section>
  )
}

// ------------------------------------------------------------------ privacy
/** A stored null means no value was set, so the default applied (for models: the deployment default). */
const settingValue = (v: unknown) => (v === null || v === undefined ? 'default' : JSON.stringify(v))

function auditDetail(e: AuditEntry): string {
  const p = e.payload as Record<string, unknown>
  switch (e.action) {
    case 'setting.changed':
      return `${e.entity_id}: from ${settingValue(p.old)} to ${settingValue(p.new)}`
    case 'backup.created':
      return `${String(p.file ?? '')}${p.reason ? `, ${String(p.reason)}` : ''}`
    case 'backup.verified':
      return `backup ${e.entity_id}: ${p.ok ? 'passed' : 'failed'}`
    case 'backup.pruned':
      return `backup ${e.entity_id} (keep ${String(p.retention)})`
    case 'backup.restored':
      return `${String(p.backup_file ?? '')}${p.reason ? `, ${String(p.reason)}` : ''}`
    case 'outbox.prepared':
      return `package ${e.entity_id}: ${(p.sections as string[] | undefined)?.join(', ') ?? ''}, days ${String(p.first_day)} to ${String(p.last_day)}${p.encrypted ? ', encrypted' : ''}`
    case 'outbox.downloaded':
      return `package ${e.entity_id}: ${String(p.file ?? '')}`
    case 'outbox.exported':
      return `package ${e.entity_id}`
    default:
      return `${e.entity} ${e.entity_id ?? ''}`
  }
}

function AuditTrail() {
  const audit = useQuery({ queryKey: ['admin', 'audit'], queryFn: () => adminApi.audit(40), refetchInterval: 30_000 })
  if (audit.isError) return <ErrorNote error={audit.error} />
  if (!audit.data) return <Loading />
  if (audit.data.length === 0) return <p className="text-sm text-ink-dim">No settings, backup or ground-report actions yet.</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <thead className="text-left text-ink-dim">
          <tr>
            <th className="pb-1 font-normal">Time</th>
            <th className="pb-1 font-normal">Person</th>
            <th className="pb-1 font-normal">Action</th>
            <th className="pb-1 font-normal">Details</th>
          </tr>
        </thead>
        <tbody>
          {audit.data.map((e) => (
            <tr key={e.id} className="border-t border-seam/60 align-top">
              <td className="num py-1.5 text-ink-dim">{fmtDateTime(e.created_at)}</td>
              <td className="py-1.5">{e.actor}</td>
              <td className="py-1.5">{AUDIT_LABEL[e.action] ?? e.action}</td>
              <td className="py-1.5 break-all text-ink-dim">{auditDetail(e)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function PrivacySection() {
  const privacy = useQuery({ queryKey: ['admin', 'privacy'], queryFn: adminApi.privacy })
  const p = privacy.data
  return (
    <Section title="Privacy">
      {privacy.isError ? <ErrorNote error={privacy.error} /> : !p ? <Loading /> : (
        <div className="flex flex-col gap-5">
          <p className="max-w-[80ch] text-sm text-ink">{p.transmission}</p>
          {p.warnings.map((w) => (
            <p key={w} role="alert" className="max-w-[80ch] rounded-md border border-sev-watch/40 bg-sev-watch/10 px-4 py-2 text-sm text-ink">
              {w}
            </p>
          ))}
          <p className="text-sm text-ink-dim">
            {p.model_service.note} <span className="num">({p.model_service.url})</span>
          </p>
          <dl className="grid gap-5 sm:grid-cols-2">
            {p.stores.map((s) => (
              <div key={s.key} className="min-w-0">
                <dt className="text-sm font-medium text-ink">{STORE_LABEL[s.key] ?? s.key}</dt>
                <dd className="text-sm text-ink-dim">{s.what}</dd>
                <dd className="num mt-0.5 break-all text-xs text-ink">{s.where}</dd>
                <dd className="mt-0.5 text-xs text-ink-faint">{s.protection}</dd>
              </div>
            ))}
          </dl>
          <p className="text-sm text-ink-dim">
            Ground reports waiting for export: <span className="num text-ink">{p.counts.ground_reports_prepared}</span>.
            Marked as exported: <span className="num text-ink">{p.counts.ground_reports_exported}</span>.
            Encrypted backups kept: <span className="num text-ink">{p.counts.backups_stored}</span>.
          </p>
        </div>
      )}
      <div className="mt-6">
        <Subheading>Data-handling audit trail</Subheading>
        <AuditTrail />
      </div>
    </Section>
  )
}

// ------------------------------------------------------------------ synchronization
function GroundReportForm() {
  const { astronaut } = useSession()
  const actor = useActor()
  const invalidate = useInvalidateAdmin()
  const overview = useQuery({
    queryKey: ['overview', astronaut?.id],
    queryFn: () => api.overview(astronaut!.id),
    enabled: !!astronaut,
  })
  const today = overview.data?.mission_day ?? null
  const [first, setFirst] = useState('')
  const [last, setLast] = useState('')
  const [sections, setSections] = useState<ReportSection[]>([])
  const [reason, setReason] = useState('')
  const [encrypt, setEncrypt] = useState(false)
  const [pass, setPass] = useState('')
  const [confirm, setConfirm] = useState('')
  const firstDay = first === '' ? Math.max(1, (today ?? 1) - 6) : Number(first)
  const lastDay = last === '' ? (today ?? 1) : Number(last)
  const create = useMutation({
    mutationFn: () =>
      adminApi.createGroundReport({
        astronaut_id: astronaut!.id,
        first_day: firstDay,
        last_day: lastDay,
        sections,
        authorized_by: actor,
        reason: reason.trim(),
        encrypt,
        ...(encrypt ? { passphrase: pass } : {}),
      }),
    onSuccess: () => {
      setPass('')
      setConfirm('')
      setReason('')
      setSections([])
      invalidate()
    },
  })

  if (!astronaut) return <p className="text-sm text-ink-dim">Select a crew member in the header to prepare a ground report.</p>
  if (overview.isError) return <ErrorNote error={overview.error} />
  if (!overview.data) return <Loading />
  if (today === null) {
    return <p className="text-sm text-ink-dim">{astronaut.display_name} has no mission yet, so there are no mission days to report.</p>
  }
  const daysValid = Number.isInteger(firstDay) && Number.isInteger(lastDay) && firstDay >= 1 && firstDay <= lastDay && lastDay <= today
  const ready = !!actor && daysValid && sections.length > 0 && reason.trim().length >= 3 && (!encrypt || passphraseReady(pass, confirm))
  const toggle = (s: ReportSection) =>
    setSections((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]))

  return (
    <form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); if (ready) create.mutate() }}>
      <p className="text-sm text-ink-dim">
        Crew member: <span className="text-ink">{astronaut.display_name} ({astronaut.code})</span>
        {astronaut.is_simulated && <> <SimulatedMark /></>}. Today is mission day <span className="num text-ink">{today}</span>.
      </p>
      <div className="flex flex-wrap gap-3">
        <Field label="First mission day">
          <input type="number" className={`${inputCls} num w-28`} min={1} max={today} value={first === '' ? firstDay : first}
            onChange={(e) => setFirst(e.target.value)} />
        </Field>
        <Field label="Last mission day">
          <input type="number" className={`${inputCls} num w-28`} min={1} max={today} value={last === '' ? lastDay : last}
            onChange={(e) => setLast(e.target.value)} />
        </Field>
      </div>
      {!daysValid && <p className="text-xs text-sev-watch">Choose days from 1 to {today}, with the first day not after the last.</p>}
      <fieldset>
        <legend className="mb-2 text-sm text-ink-dim">Sections to include (nothing else is written to the package)</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {REPORT_SECTIONS.map((s) => (
            <label key={s} className="flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-0.5 size-4 accent-[var(--color-signal)]" checked={sections.includes(s)}
                onChange={() => toggle(s)} />
              <span>
                <span className="text-ink">{SECTION_LABEL[s]}</span>
                <span className="block text-xs text-ink-faint">{SECTION_HINT[s]}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <Field label="Reason for sending">
        <input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. Weekly private medical conference" />
      </Field>
      <label className="inline-flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" className="size-4 accent-[var(--color-signal)]" checked={encrypt}
          onChange={(e) => setEncrypt(e.target.checked)} />
        Encrypt the package with a passphrase
      </label>
      {encrypt && (
        <div className="flex flex-col gap-2 pl-6">
          <PassphraseFields pass={pass} confirm={confirm} onPass={setPass} onConfirm={setConfirm} />
          <p className="text-xs text-ink-faint">Give the passphrase to the ground team through a separate channel. AMC does not store it.</p>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={!ready || create.isPending}>
          {create.isPending ? 'Preparing…' : 'Prepare ground report'}
        </Button>
        {actor ? <span className="text-sm text-ink-dim">Authorized by {actor}</span> : <NeedsName />}
      </div>
      <Problem error={create.error} />
      {create.data && (
        <p role="status" className="text-sm text-sev-normal">
          Package {create.data.id} prepared with{' '}
          {Object.values(create.data.record_counts).reduce((a, b) => a + b, 0)} records. Download it from the outbox below.
        </p>
      )}
    </form>
  )
}

function PackageRow({ p }: { p: OutboxPackage }) {
  const actor = useActor()
  const invalidate = useInvalidateAdmin()
  const { astronauts } = useSession()
  const [confirming, setConfirming] = useState(false)
  const download = useMutation({
    mutationFn: (file: string) => adminApi.downloadPackageFile(p.id, file, actor),
    onSuccess: invalidate,
  })
  const exported = useMutation({
    mutationFn: () => adminApi.markExported(p.id, actor),
    onSuccess: () => {
      setConfirming(false)
      invalidate()
    },
  })
  const crew = astronauts.find((a) => a.id === p.astronaut_id)
  return (
    <tr className="border-t border-seam/60 align-top">
      <td className="num py-2 text-ink-dim">{fmtDateTime(p.created_at)}</td>
      <td className="py-2">
        {crew ? crew.code : `#${p.astronaut_id}`} {p.simulated && <SimulatedMark title="The package contains simulated records." />}
      </td>
      <td className="num py-2">{p.first_day} to {p.last_day}</td>
      <td className="py-2 text-ink-dim">{p.sections.map((s) => SECTION_LABEL[s as ReportSection] ?? s).join(', ')}</td>
      <td className="py-2">
        {p.authorized_by}
        <span className="block text-xs text-ink-faint">{p.reason}</span>
      </td>
      <td className="py-2">
        <div className="flex flex-col items-start gap-1">
          {p.files.map((f) => (
            <button key={f.name} type="button" onClick={() => download.mutate(f.name)} disabled={!actor || download.isPending}
              title={`SHA-256 ${f.sha256}`}
              className="num text-left text-xs text-signal underline-offset-2 hover:underline disabled:cursor-not-allowed disabled:text-ink-faint">
              {f.name} ({fmtBytes(f.size_bytes)})
            </button>
          ))}
        </div>
        <Problem error={download.error} />
      </td>
      <td className="py-2">
        {p.status === 'exported' ? (
          <span className="text-sev-normal">
            Exported
            <span className="block text-xs text-ink-faint">
              marked by {p.exported_by}{p.exported_at ? `, ${fmtDateTime(p.exported_at)}` : ''}
            </span>
          </span>
        ) : confirming ? (
          <div className="flex flex-col items-start gap-1">
            <span className="text-xs text-ink-dim">Only after the file was sent over the approved link.</span>
            <div className="flex gap-2">
              <Button onClick={() => exported.mutate()} disabled={!actor || exported.isPending}>Confirm</Button>
              <Button variant="quiet" onClick={() => setConfirming(false)}>Cancel</Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-start gap-1">
            <span>Waiting for export</span>
            <Button variant="quiet" onClick={() => setConfirming(true)} disabled={!actor}>Mark as exported</Button>
          </div>
        )}
        <Problem error={exported.error} />
      </td>
    </tr>
  )
}

function OutboxList() {
  const actor = useActor()
  const list = useQuery({ queryKey: ['admin', 'outbox'], queryFn: adminApi.outbox })
  if (list.isError) return <ErrorNote error={list.error} />
  if (!list.data) return <Loading />
  if (list.data.length === 0) return <Empty title="The outbox is empty">Prepared ground reports appear here.</Empty>
  return (
    <div className="overflow-x-auto">
      {!actor && <p className="mb-2"><NeedsName /></p>}
      <table className="w-full min-w-[860px] text-sm">
        <thead className="text-left text-ink-dim">
          <tr>
            <th className="pb-1 font-normal">Prepared</th>
            <th className="pb-1 font-normal">Crew</th>
            <th className="pb-1 font-normal">Days</th>
            <th className="pb-1 font-normal">Sections</th>
            <th className="pb-1 font-normal">Authorized by</th>
            <th className="pb-1 font-normal">Files</th>
            <th className="pb-1 font-normal">Status</th>
          </tr>
        </thead>
        <tbody>
          {list.data.map((p) => <PackageRow key={p.id} p={p} />)}
        </tbody>
      </table>
    </div>
  )
}

function SyncSection() {
  return (
    <Section title="Synchronization">
      <p className="mb-5 max-w-[80ch] text-sm text-ink-dim">
        AMC never sends anything by itself. A ground report holds only the crew member, days and sections you select,
        and records who authorized it. A person moves the file over the mission&apos;s approved link and then marks it as
        exported. Every package carries record ids, the DEMO threshold label and a simulated-data flag.
      </p>
      <div className="flex flex-col gap-8">
        <GroundReportForm />
        <div>
          <Subheading>Outbox</Subheading>
          <OutboxList />
        </div>
      </div>
    </Section>
  )
}

// ------------------------------------------------------------------ diagnostics
function DiagnosticsSection({ diag }: { diag: UseQueryResult<Diagnostics> }) {
  const d = diag.data
  return (
    <Section
      title="Diagnostics"
      aside={
        <Button variant="quiet" onClick={() => diag.refetch()} disabled={diag.isFetching}>
          {diag.isFetching ? 'Measuring…' : 'Refresh'}
        </Button>
      }
    >
      {diag.isError ? <ErrorNote error={diag.error} /> : !d ? <Loading /> : (
        <div className="grid gap-8 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <KeyValue
            items={[
              ['Database file', <span className="num break-all text-sm">{d.database.path ?? d.database.engine}</span>],
              ['Database size', <span className="num">{fmtBytes(d.database.size_bytes)}, write-ahead log {fmtBytes(d.database.wal_bytes)}</span>],
              ['Schema revision', (
                <span className="num">
                  {d.database.alembic_revision ?? 'none'}{' '}
                  {d.database.at_head ? <span className="text-sev-normal">(latest)</span>
                    : <span className="text-sev-warning">(latest is {d.database.alembic_head ?? 'unknown'})</span>}
                </span>
              )],
              ['Free space', <span className="num">{fmtBytes(d.storage.free_bytes)} of {fmtBytes(d.storage.total_bytes)}</span>],
              ['Data folder', <span className="num break-all text-sm">{d.storage.data_dir}</span>],
              ['Backups and outbox', <span className="num">{fmtBytes(d.storage.backups_bytes)} and {fmtBytes(d.storage.outbox_bytes)}</span>],
              ['Model service', d.ollama.reachable
                ? <span className="text-sev-normal">Reachable at {d.ollama.url}</span>
                : <span className="text-ink-dim">Not reachable ({d.ollama.reason ?? 'unknown'}). Monitoring does not need it.</span>],
              ['Installed models', d.ollama.reachable ? (d.ollama.installed_models.join(', ') || 'none') : '–'],
              ['Chat model', <span><span className="num">{d.ollama.chat_model}</span> ({d.ollama.chat_model_source})</span>],
              ['Embedding model', <span><span className="num">{d.ollama.embed_model}</span> ({d.ollama.embed_model_source})</span>],
              ['Embedding coverage', d.embeddings.chunks_total === 0 ? 'No knowledge passages indexed yet' : (
                <span>
                  <span className="num">{d.embeddings.chunks_current_model} of {d.embeddings.chunks_total}</span> passages
                  ({d.embeddings.coverage_percent ?? 0}%), search method: {d.embeddings.search_method}
                </span>
              )],
              ['Last backup', d.backups.last_backup_at ? (
                <span>
                  <span className="num">{fmtDateTime(d.backups.last_backup_at)}</span> (backup {d.backups.last_backup_id}),{' '}
                  {d.backups.stored} kept, retention {d.backups.retention}
                </span>
              ) : <span className="text-sev-watch">No backup yet</span>],
              ['AMC version', <span className="num">{d.versions.app}</span>],
              ['Runtime', <span className="num">Python {d.versions.python}, SQLite {d.versions.sqlite}, SQLAlchemy {d.versions.sqlalchemy}, FastAPI {d.versions.fastapi}</span>],
              ['Operating system', d.versions.os],
              ['Thresholds', <span className="text-sev-watch">{d.versions.thresholds_label} ({d.versions.thresholds_version})</span>],
              ['Baseline policy', <span className="num">{d.versions.baseline_policy_version}</span>],
            ]}
          />
          <div>
            <Subheading>Rows per table</Subheading>
            <table className="w-full text-sm">
              <tbody>
                {Object.entries(d.database.row_counts).map(([table, n]) => (
                  <tr key={table} className="border-t border-seam/60">
                    <td className="py-1 text-ink-dim">{table.replaceAll('_', ' ')}</td>
                    <td className="num py-1 text-right">{n.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-ink-faint">Measured {fmtDateTime(d.generated_at)}.</p>
          </div>
        </div>
      )}
    </Section>
  )
}

// ------------------------------------------------------------------ page
export function SettingsPage() {
  const { operator, setOperator } = useSession()
  const settings = useQuery({ queryKey: ['admin', 'settings'], queryFn: adminApi.settings })
  const diag = useQuery({ queryKey: ['admin', 'diagnostics'], queryFn: adminApi.diagnostics, refetchInterval: 60_000 })
  return (
    <div className="flex flex-col gap-10">
      <div className="flex flex-wrap items-end gap-4">
        <div className="mr-auto">
          <h1 className="text-2xl font-semibold">Settings</h1>
          <p className="mt-1 max-w-[70ch] text-sm text-ink-dim">
            Local model, backups, privacy, ground reports and diagnostics. Each change is recorded in the audit trail with
            your name.
          </p>
        </div>
        <Field label="Your name">
          <input className={`${inputCls} w-56`} value={operator} onChange={(e) => setOperator(e.target.value)}
            placeholder="e.g. Crew medical officer" />
        </Field>
      </div>
      <ModelSection settings={settings} diag={diag} />
      <DataSection settings={settings} />
      <PrivacySection />
      <SyncSection />
      <DiagnosticsSection diag={diag} />
    </div>
  )
}
