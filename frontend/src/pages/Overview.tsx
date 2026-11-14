import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type LatestReading, type ThresholdRule } from '../api/client'
import { BodyMap } from '../components/BodyMap'
import { CurrentGuidance, OpenEscalations } from '../components/Guidance'
import { DeviationStrip } from '../components/deviation'
import { DemoThresholdNote, Empty, ErrorNote, Loading, Section, SeverityTag, SimulatedMark } from '../components/ui'
import { contextLabel, fmtAgo, fmtNum, fmtZ, severityColor, titleCase, unitLabel } from '../lib/format'
import { useSession } from '../state/session'

export function NoCrew() {
  return (
    <Empty title="No crew records yet">
      Add a crew member and a mission, or generate a labelled simulated mission, in{' '}
      <Link to="/system" className="text-signal underline underline-offset-2">System</Link>.
    </Empty>
  )
}

function ReadingRow({ r, rule }: { r: LatestReading; rule: ThresholdRule | undefined }) {
  const m = r.measurement
  const assessed = m.assessment_status === 'assessed'
  return (
    <tr className="border-b border-seam/60 last:border-0">
      <td className="py-2.5 pr-2 sm:pr-4">
        <Link
          to={`/health?metric=${r.metric_code}&context=${r.context}`}
          className="font-medium text-ink hover:text-signal"
        >
          {r.display_name}
        </Link>
        <div className="text-xs text-ink-dim">{contextLabel(r.context)}</div>
      </td>
      <td className="num whitespace-nowrap py-2.5 pr-2 text-right text-base sm:pr-4 sm:text-lg">
        {fmtNum(m.value)}
        <span className="ml-1 text-xs text-ink-dim">{unitLabel(m.unit)}</span>
      </td>
      <td className="num hidden py-2.5 pr-4 text-right text-sm text-ink-dim sm:table-cell">
        {r.baseline ? fmtNum(r.baseline.median) : '–'}
      </td>
      <td className="w-[34%] min-w-14 py-2.5 pr-2 sm:min-w-32 sm:pr-4">
        {assessed ? (
          <DeviationStrip z={m.deviation_score} severity={m.severity} rule={rule} />
        ) : (
          <span className="text-xs text-ink-faint">
            {!r.baseline_enabled ? 'Logged only, not compared' : r.baseline ? 'Trend only, no alert rule' : 'Collecting baseline data'}
          </span>
        )}
      </td>
      <td className="num whitespace-nowrap py-2.5 pr-1 text-right text-sm sm:pr-4" style={{ color: assessed ? severityColor(m.severity) : undefined }}>
        {assessed ? fmtZ(m.deviation_score) : ''}
      </td>
      <td className="hidden py-2.5 text-right text-xs text-ink-faint md:table-cell">{fmtAgo(m.recorded_at)}</td>
    </tr>
  )
}

export function OverviewPage() {
  const { astronaut, astronautsLoading } = useSession()
  const overview = useQuery({
    queryKey: ['overview', astronaut?.id],
    queryFn: () => api.overview(astronaut!.id),
    enabled: !!astronaut,
    refetchInterval: 15_000,
  })
  const thresholds = useQuery({ queryKey: ['thresholds'], queryFn: api.thresholds })

  if (astronautsLoading) return <Loading />
  if (!astronaut) return <NoCrew />
  if (overview.isError) return <ErrorNote error={overview.error} />
  if (!overview.data) return <Loading />

  const ov = overview.data
  const rules = new Map(thresholds.data?.rules.map((r) => [r.metric_code, r]))
  const byCategory = new Map<string, LatestReading[]>()
  for (const r of ov.latest) byCategory.set(r.category, [...(byCategory.get(r.category) ?? []), r])

  return (
    <div className="flex flex-col gap-8">
      {/* Mission header: the astronaut's current state in one line of sight */}
      <div className="grid gap-6 border-b border-seam pb-6 md:grid-cols-[1fr_auto]">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-3xl font-semibold leading-tight">{ov.astronaut.display_name}</h1>
            {ov.simulated && <SimulatedMark />}
          </div>
          <p className="mt-1 text-ink-dim">
            {ov.mission ? ov.mission.name : 'No mission assigned'}
            {ov.mission_day != null && (
              <>
                {', '}mission day <span className="num text-ink">{ov.mission_day}</span>
                {ov.mission?.planned_days ? <> of <span className="num">{ov.mission.planned_days}</span></> : null}
              </>
            )}
          </p>
        </div>
        <div className="flex items-end gap-8">
          <div>
            <p className="text-sm text-ink-dim">Health status</p>
            <SeverityTag severity={ov.status === 'UNASSESSED' ? null : ov.status} size="lg" />
          </div>
          <div>
            <p className="text-sm text-ink-dim">Open alerts</p>
            <p className="num text-lg">{ov.open_alerts.length}</p>
          </div>
          <div>
            <p className="text-sm text-ink-dim">Readings stored</p>
            <p className="num text-lg">{ov.measurement_count.toLocaleString()}</p>
          </div>
        </div>
      </div>

      {/* Where it is, and how serious: body systems beside the plain-language guidance */}
      <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Section title="Body map" aside="Select a system for details">
          <BodyMap astronautId={ov.astronaut.id} />
        </Section>
        <div className="flex min-w-0 flex-col gap-8">
          <CurrentGuidance astronautId={ov.astronaut.id} />
          <OpenEscalations astronautId={ov.astronaut.id} />
        </div>
      </div>

      <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Section
          title="Latest readings against personal baseline"
          aside={thresholds.data && <DemoThresholdNote label={thresholds.data.label} />}
        >
          {ov.latest.length === 0 ? (
            <Empty title="No readings recorded">Measurements appear here as soon as they are ingested.</Empty>
          ) : (
            <div className="flex flex-col gap-6">
              {[...byCategory.entries()].map(([cat, rows]) => (
                <div key={cat}>
                  <h3 className="mb-1 text-sm font-medium text-ink-dim">{titleCase(cat)}</h3>
                  {/* on very narrow screens the table scrolls inside this box, never the page */}
                  <div className="overflow-x-auto">
                    <table className="w-full text-left">
                      <thead className="sr-only">
                        <tr><th>Indicator</th><th>Latest</th><th>Median</th><th>Deviation</th><th>Score</th><th>When</th></tr>
                      </thead>
                      <tbody>
                        {rows.map((r) => (
                          <ReadingRow key={`${r.metric_code}-${r.context}`} r={r} rule={rules.get(r.metric_code)} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
              <p className="text-xs text-ink-faint">
                The strip spans ±6σ around the personal median. Shaded zones are the configured watch, warning and
                critical bands. Readings are compared only with a baseline from the same context.
              </p>
            </div>
          )}
        </Section>

        <Section title="Open alerts" aside={<Link to="/alerts" className="text-signal hover:underline">All alerts</Link>}>
          {ov.open_alerts.length === 0 ? (
            <p className="text-sm text-ink-dim">No open alerts.</p>
          ) : (
            <ul className="flex flex-col">
              {ov.open_alerts.map((a) => (
                <li key={a.id} className="border-b border-seam/60 last:border-0">
                  <Link to={`/alerts?id=${a.id}`} className="block py-2.5 hover:bg-deck/40">
                    <div className="flex items-center justify-between gap-3">
                      <SeverityTag severity={a.severity} />
                      <span className="text-xs text-ink-faint">{fmtAgo(a.last_seen_at)}</span>
                    </div>
                    <p className="mt-0.5 text-sm text-ink">
                      {a.metric_code.replaceAll('_', ' ')}, {contextLabel(a.context).toLowerCase()}, {a.direction}{' '}
                      <span className="num text-ink-dim">peak {fmtZ(a.peak_deviation)}</span>
                    </p>
                    {a.status === 'acknowledged' && <p className="text-xs text-ink-dim">Acknowledged</p>}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>
    </div>
  )
}
