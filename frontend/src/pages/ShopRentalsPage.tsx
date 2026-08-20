/**
 * Feature 2: shop-rental applications (erhvervslejemål).
 *
 * A filterable list on the left, the selected application on the right — the
 * same master-detail shape as the calendar page, because the job is the same:
 * skim many, work on one.
 *
 * Only reachable by members of the erhvervsudvalg. The route is gated in
 * App.tsx and every endpoint is gated by IsBusinessCommittee. Both checks
 * matter: the client-side one is convenience, the server-side one is the real
 * boundary.
 *
 * The statuses exist for reasons worth keeping in mind while reading this:
 * "Gemt til senere" is a good applicant with no vacant unit to offer, and
 * "I gang" routinely lasts months, so the default view is the three live piles
 * rather than everything ever received.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import { STATUS_LABELS, STATUS_ORDER, shopRentals } from '../api/shoprentals'
import { ApplicationCard } from '../components/ApplicationCard'
import { ApplicationDetail } from '../components/ApplicationDetail'

import type {
  Application,
  ApplicationStatus,
  CommitteeMember,
  StatusSummary,
} from '../api/shoprentals'

/** What the committee wants to see on opening the page: everything still in
 *  play, and none of what has been dealt with. */
const DEFAULT_STATUSES: ApplicationStatus[] = ['new', 'in_progress', 'saved']

const ORDERINGS: [string, string][] = [
  ['-submitted_at', 'Nyeste først'],
  ['submitted_at', 'Ældste først'],
  ['-rating', 'Bedst vurderet'],
  ['-status_changed_at', 'Senest ændret'],
]

export function ShopRentalsPage() {
  const [statuses, setStatuses] = useState<ApplicationStatus[]>(DEFAULT_STATUSES)
  const [assignee, setAssignee] = useState('')
  const [ordering, setOrdering] = useState('-submitted_at')
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')

  const [applications, setApplications] = useState<Application[]>([])
  const [count, setCount] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [page, setPage] = useState(1)
  const [members, setMembers] = useState<CommitteeMember[]>([])
  const [summary, setSummary] = useState<StatusSummary | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const filters = useMemo(
    () => ({ status: statuses, assignee, ordering, q: query }),
    [statuses, assignee, ordering, query],
  )

  // Debounced, so typing in the search box does not fire a request per keystroke.
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [search])

  // The members list and the status counts do not depend on the filters.
  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const [loadedMembers, loadedSummary] = await Promise.all([
          shopRentals.members(),
          shopRentals.summary(),
        ])
        if (!cancelled) {
          setMembers(loadedMembers)
          setSummary(loadedSummary)
        }
      } catch {
        // Not fatal: the list is still usable without counts or an assignee
        // dropdown, so this does not take over the page with an error.
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setPage(1)

    async function load() {
      setLoading(true)
      try {
        const result = await shopRentals.list(filters)
        if (!cancelled) {
          setApplications(result.results)
          setCount(result.count)
          setHasMore(result.next !== null)
          setError(null)
        }
      } catch {
        if (!cancelled) setError('Ansøgningerne kunne ikke hentes.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [filters])

  const loadMore = useCallback(async () => {
    const next = page + 1
    try {
      const result = await shopRentals.list({ ...filters, page: next })
      setApplications((current) => [...current, ...result.results])
      setHasMore(result.next !== null)
      setPage(next)
    } catch {
      setError('Der kunne ikke hentes flere.')
    }
  }, [filters, page])

  function toggleStatus(status: ApplicationStatus) {
    setStatuses((current) =>
      current.includes(status)
        ? current.filter((value) => value !== status)
        : [...current, status],
    )
  }

  /** Replace one application in place after an edit, and refresh the counts —
   *  changing a status moves it between piles, so the chips would otherwise lie. */
  const applyChange = useCallback((updated: Application) => {
    setApplications((current) =>
      current.map((application) => (application.id === updated.id ? updated : application)),
    )
    void shopRentals.summary().then(setSummary).catch(() => undefined)
  }, [])

  const selected = applications.find((application) => application.id === selectedId) ?? null

  return (
    <div className="erhverv-page">
      <section className="erhverv-list">
        <div className="filters card">
          <div className="filters__chips" role="group" aria-label="Filtrér på status">
            {STATUS_ORDER.map((status) => (
              <button
                key={status}
                type="button"
                className={`chip${statuses.includes(status) ? ' chip--on' : ''}`}
                aria-pressed={statuses.includes(status)}
                onClick={() => toggleStatus(status)}
              >
                {STATUS_LABELS[status]}
                {summary && <span className="chip__count">{summary.by_status[status]}</span>}
              </button>
            ))}
          </div>

          <input
            type="search"
            value={search}
            placeholder="Søg i navn, email og alle svar…"
            aria-label="Søg"
            onChange={(event) => setSearch(event.target.value)}
          />

          <div className="filters__row">
            <label>
              Ansvarlig
              <select value={assignee} onChange={(event) => setAssignee(event.target.value)}>
                <option value="">Alle</option>
                <option value="unassigned">Ikke tildelt</option>
                {members.map((member) => (
                  <option key={member.id} value={String(member.id)}>
                    {member.name}
                  </option>
                ))}
              </select>
            </label>

            <label>
              Sortering
              <select value={ordering} onChange={(event) => setOrdering(event.target.value)}>
                {ORDERINGS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {statuses.length === 0 && (
            <p className="hint">Ingen status valgt — viser alle ansøgninger.</p>
          )}
        </div>

        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}

        {loading ? (
          <p className="status">Indlæser…</p>
        ) : applications.length === 0 ? (
          <p className="status">Ingen ansøgninger matcher.</p>
        ) : (
          <>
            <p className="erhverv-list__count">
              {count === 1 ? '1 ansøgning' : `${count} ansøgninger`}
            </p>
            <div className="erhverv-list__rows">
              {applications.map((application) => (
                <ApplicationCard
                  key={application.id}
                  application={application}
                  selected={application.id === selectedId}
                  onSelect={() => setSelectedId(application.id)}
                />
              ))}
            </div>
            {hasMore && (
              <button type="button" className="button--quiet" onClick={() => void loadMore()}>
                Hent flere
              </button>
            )}
          </>
        )}
      </section>

      {selected ? (
        <ApplicationDetail application={selected} members={members} onChanged={applyChange} />
      ) : (
        <aside className="card status">Vælg en ansøgning for at se den.</aside>
      )}
    </div>
  )
}
