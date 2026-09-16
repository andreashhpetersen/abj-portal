import { useMemo, useState } from 'react'

import type { BookingEvent } from '../api/bookings'
import { formatDayShort, formatMonth, formatTimeRange, isSameMonth } from '../lib/dates'
import { EventDetails } from './EventDetails'

interface Props {
  month: Date
  /** The same events the grid draws — fetched for the whole visible grid, so
   *  this is a superset of the month and needs narrowing, not another request. */
  events: BookingEvent[]
  onChanged: () => Promise<void> | void
}

/**
 * The month's shared arrangements as a list: what it is, when it is, and the
 * rest only when you ask for it.
 *
 * A row is judged by the day it *starts*, the same way the booking rules judge
 * a weekday — a party running past midnight on the 31st belongs to the month it
 * began in, not to the one it spilled into.
 */
export function PublicEventList({ month, events, onChanged }: Props) {
  const [expanded, setExpanded] = useState<number | null>(null)

  const listed = useMemo(
    () =>
      events
        .filter((event) => event.category === 'public' && isSameMonth(new Date(event.start), month))
        .sort((left, right) => left.start.localeCompare(right.start)),
    [events, month],
  )

  if (listed.length === 0) {
    return (
      <p className="event-list__empty">Ingen fælles arrangementer i {formatMonth(month)}.</p>
    )
  }

  return (
    <ul className="event-list">
      {listed.map((event) => {
        const open = event.id === expanded
        return (
          <li key={event.id} className={`event-list__item${open ? ' event-list__item--open' : ''}`}>
            <button
              type="button"
              className="event-list__summary"
              aria-expanded={open}
              aria-controls={`event-${event.id}-details`}
              onClick={() => setExpanded(open ? null : event.id)}
            >
              <span className="event-list__marker" aria-hidden="true">
                {open ? '▾' : '▸'}
              </span>
              <span className="event-list__title">{event.title}</span>
              <span className="event-list__when">
                {formatDayShort(event.start)} · {formatTimeRange(event.start, event.end)}
              </span>
            </button>

            {open && (
              <div id={`event-${event.id}-details`} className="event-list__details">
                <EventDetails event={event} onChanged={onChanged} />
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}
