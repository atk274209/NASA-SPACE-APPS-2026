// Settings, diagnostics, encrypted backups and the ground-report outbox (feature F6).
// Types are written by hand to match the Pydantic schemas in backend/am/api/v1/admin.py.
import { ApiError, request, type AuditEntry } from './client'

export type SettingKind = 'model' | 'bool' | 'int' | 'text'
export type SettingValue = string | number | boolean | null

export interface Setting {
  key: string
  kind: SettingKind
  value: SettingValue
  default: SettingValue
  effective: SettingValue
  deployment_default: string | null
  is_default: boolean
  description: string
  min_value: number | null
  max_value: number | null
  updated_by: string | null
  updated_at: string | null
}

export interface Diagnostics {
  generated_at: string
  database: {
    engine: string
    path: string | null
    size_bytes: number | null
    wal_bytes: number
    alembic_revision: string | null
    alembic_head: string | null
    at_head: boolean
    row_counts: Record<string, number>
  }
  storage: {
    data_dir: string
    backups_dir: string
    outbox_dir: string
    free_bytes: number
    total_bytes: number
    backups_bytes: number
    outbox_bytes: number
  }
  ollama: {
    url: string
    reachable: boolean
    reason: string | null
    installed_models: string[]
    chat_model: string
    chat_model_source: string
    chat_model_installed: boolean | null
    embed_model: string
    embed_model_source: string
    embed_model_installed: boolean | null
  }
  embeddings: {
    embed_model: string
    chunks_total: number
    chunks_current_model: number
    chunks_other_model: number
    chunks_without_embedding: number
    coverage_percent: number | null
    search_method: string
  }
  backups: { last_backup_at: string | null; last_backup_id: number | null; stored: number; retention: number }
  versions: {
    app: string
    python: string
    sqlite: string
    sqlalchemy: string
    fastapi: string
    os: string
    thresholds_version: string
    thresholds_label: string
    baseline_policy_version: string
  }
}

export interface Privacy {
  stores: { key: string; what: string; where: string; protection: string }[]
  model_service: { url: string; host: string; is_local: boolean; note: string }
  transmission: string
  warnings: string[]
  counts: Record<string, number>
}

export interface Backup {
  id: number
  created_at: string
  authorized_by: string
  reason: string
  status: 'stored' | 'pruned' | string
  file_name: string | null
  location: string | null
  file_present: boolean
  sha256: string | null
  size_bytes: number | null
  plaintext_bytes: number | null
  encrypted: boolean
  cipher: string | null
  kdf: Record<string, string | number>
  alembic_revision: string | null
  pruned_backup_ids: number[]
}

export interface BackupVerification {
  backup_id: number
  ok: boolean
  checksum_ok: boolean
  decrypted: boolean
  integrity: string[]
  alembic_revision: string | null
  tables: number | null
  message: string
  verified_at: string
}

export const REPORT_SECTIONS = ['alerts', 'journal', 'observations', 'symptoms', 'checkins', 'missions'] as const
export type ReportSection = (typeof REPORT_SECTIONS)[number]

export interface GroundReportRequest {
  astronaut_id: number
  first_day: number
  last_day: number
  sections: ReportSection[]
  authorized_by: string
  reason: string
  encrypt: boolean
  passphrase?: string
}

export interface PackageFile {
  name: string
  media_type: string
  sha256: string
  size_bytes: number
}

export interface OutboxPackage {
  id: number
  kind: string
  astronaut_id: number | null
  created_at: string
  authorized_by: string
  reason: string
  status: 'prepared' | 'exported' | string
  encrypted: boolean
  sha256: string | null
  size_bytes: number | null
  location: string | null
  first_day: number | null
  last_day: number | null
  sections: string[]
  files: PackageFile[]
  record_counts: Record<string, number>
  simulated: boolean
  thresholds_label: string | null
  exported_by: string | null
  exported_at: string | null
}

/** Fetch a package file and hand it to the browser as a download.
 *  The person's name travels in a header (percent-encoded), never in the URL, and the
 *  backend records the download in the audit trail. */
async function downloadPackageFile(id: number, file: string, actor: string): Promise<void> {
  let res: Response
  try {
    res = await fetch(`/api/v1/admin/outbox/${id}/download?file=${encodeURIComponent(file)}`, {
      headers: actor.trim() ? { 'X-AM-Actor': encodeURIComponent(actor.trim()) } : undefined,
    })
  } catch {
    throw new ApiError(0, 'The AMC service is not reachable. Start the backend on port 8000.')
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail)
  }
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = file
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1_000)
}

export const adminApi = {
  settings: () => request<Setting[]>('GET', '/settings'),
  updateSetting: (key: string, value: SettingValue, actor: string) =>
    request<Setting>('PUT', `/settings/${encodeURIComponent(key)}`, { value, actor }),
  diagnostics: () => request<Diagnostics>('GET', '/admin/diagnostics'),
  privacy: () => request<Privacy>('GET', '/admin/privacy'),
  audit: (limit = 40) => request<AuditEntry[]>('GET', '/admin/audit', undefined, { limit }),
  backups: () => request<Backup[]>('GET', '/admin/backups'),
  createBackup: (body: { passphrase: string; actor: string; reason: string }) =>
    request<Backup>('POST', '/admin/backups', body),
  verifyBackup: (id: number, passphrase: string, actor: string) =>
    request<BackupVerification>('POST', `/admin/backups/${id}/verify`, { passphrase, actor }),
  outbox: () => request<OutboxPackage[]>('GET', '/admin/outbox'),
  createGroundReport: (body: GroundReportRequest) =>
    request<OutboxPackage>('POST', '/admin/outbox/ground-report', body),
  markExported: (id: number, actor: string) =>
    request<OutboxPackage>('POST', `/admin/outbox/${id}/mark-exported`, { actor }),
  downloadPackageFile,
}
