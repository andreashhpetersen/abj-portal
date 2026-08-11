import { useState } from 'react'

import type { BookingEvent } from '../api/bookings'
import { bookings } from '../api/bookings'
import { formatTimeRange } from '../lib/dates'

interface Props {
  event: BookingEvent
  onChanged: () => Promise<void> | void
}

/**
 * One booking. Private ones deliberately show no title — only when the room is
 * taken and who to ask about it, which is what the calendar is for.
 */
export function EventCard({ event, onChanged }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isPublic = event.category === 'public'

  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action()
      await onChanged()
    } catch {
      setError('Handlingen kunne ikke gennemføres. Prøv igen.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <article className={`event event--${event.category}`}>
      <div className="event__when">{formatTimeRange(event.start, event.end)}</div>

      <h3 className="event__title">{isPublic ? event.title : 'Privat booking'}</h3>
      {isPublic && event.description && <p className="event__body">{event.description}</p>}

      <p className="event__contact">
        Booket af {event.created_by.name || event.created_by.email}
        {' · '}
        <a href={`mailto:${event.created_by.email}`}>{event.created_by.email}</a>
        {event.created_by.phone && (
          <>
            {' · '}
            <a href={`tel:${event.created_by.phone.replace(/\s/g, '')}`}>
              {event.created_by.phone}
            </a>
          </>
        )}
      </p>

      {isPublic && (
        <p className="event__attendance">
          {event.attendee_count === 1 ? '1 deltager' : `${event.attendee_count} deltagere`}
        </p>
      )}

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      <div className="event__actions">
        {isPublic && (
          <button
            type="button"
            className={event.is_attending ? 'button--quiet' : ''}
            disabled={busy}
            onClick={() =>
              run(() => (event.is_attending ? bookings.withdraw(event.id) : bookings.attend(event.id)))
            }
          >
            {event.is_attending ? 'Meld fra' : 'Jeg deltager'}
          </button>
        )}
        {event.can_cancel && (
          <button
            type="button"
            className="button--danger"
            disabled={busy}
            onClick={() => {
              if (window.confirm('Er du sikker på, at du vil aflyse denne booking?')) {
                void run(() => bookings.cancel(event.id))
              }
            }}
          >
            Aflys
          </button>
        )}
      </div>
    </article>
  )
}
