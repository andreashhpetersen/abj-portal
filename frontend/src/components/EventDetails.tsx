import { useState } from 'react'

import type { BookingEvent } from '../api/bookings'
import { bookings } from '../api/bookings'

interface Props {
  event: BookingEvent
  onChanged: () => Promise<void> | void
}

/**
 * A booking, minus when it is and what it is called.
 *
 * Its own component because the day panel and the month's event list show the
 * same thing under different headings, and the attend and cancel buttons in
 * particular must not be written twice: what they are allowed to do is decided
 * by the server and read straight off the event.
 */
export function EventDetails({ event, onChanged }: Props) {
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
    <>
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
              run(() =>
                event.is_attending ? bookings.withdraw(event.id) : bookings.attend(event.id),
              )
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
    </>
  )
}
