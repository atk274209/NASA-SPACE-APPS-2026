// The astronaut's side of doctor approval (master prompt Module D / O "safety shift"):
// plain, direct wording; only the approved DEMO protocol meanwhile; never treatment advice.
import { useQuery } from '@tanstack/react-query'
import { doctorApi } from '../api/doctor'
import { fmtDateTime } from '../lib/format'

export function CareBanner({ astronautId }: { astronautId: number }) {
  const q = useQuery({ queryKey: ['care', astronautId], queryFn: () => doctorApi.care(astronautId), refetchInterval: 5_000 })
  if (!q.data) return null
  const { awaiting, recently_approved: approved, protocol, protocol_label, am_core_notices: notices } = q.data
  const recentNotices = (notices ?? []).filter((n) => n.kind === 'emergency').slice(0, 2)
  if (!awaiting.length && !approved.length && !recentNotices.length) return null
  return (
    <div className="grid gap-3">
      {awaiting.length > 0 && (
        <section role="alert" className="rounded-md border border-sev-critical/60 bg-sev-critical/10 p-4">
          <h2 className="font-semibold text-ink">Sent to the doctor: waiting for approval</h2>
          <p className="mt-1 text-sm text-ink">
            What you reported may need attention. It went to the flight surgeon at once and is waiting for their
            approval. AMC does not diagnose or give treatment.
          </p>
          <ul className="mt-2 list-disc pl-5 text-sm text-ink-dim">
            {awaiting.map((i) => <li key={i.id}>“{i.text}” <span className="num text-ink-faint">({fmtDateTime(i.created_at)})</span></li>)}
          </ul>
          <p className="mt-3 text-sm text-ink">
            <strong>Meanwhile ({protocol.procedure_id}):</strong> {protocol.plain_meaning}. Involve: {protocol.who_to_involve}. {protocol.guidance}.
          </p>
          <p className="mt-1 text-xs text-ink-faint">{protocol_label}</p>
        </section>
      )}
      {recentNotices.map((n, i) => (
        <section key={i} className="rounded-md border border-sev-watch/50 bg-sev-watch/10 p-3 text-sm">
          <span className="font-semibold text-ink">AMC Core was opened</span>{' '}
          <span className="text-ink-dim">by {n.actor} on {fmtDateTime(n.opened_at)} for an emergency: “{n.reason}”.</span>
        </section>
      ))}
      {approved.length > 0 && (
        <section className="rounded-md border border-sev-normal/50 bg-sev-normal/10 p-4">
          <h2 className="font-semibold text-ink">From your doctor</h2>
          <ul className="mt-1 grid gap-1 text-sm">
            {approved.map((i) => (
              <li key={i.id}>
                <span className="text-ink-dim">About “{i.text.slice(0, 80)}{i.text.length > 80 ? '…' : ''}”: </span>
                <span className="text-ink">{i.approval_note || 'Reviewed.'}</span>
                <span className="num text-xs text-ink-faint"> · {i.approval_by}{i.approval_at && `, ${fmtDateTime(i.approval_at)}`}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
