import { useState } from 'react'

import type { BookingEvent, BookingPolicy } from '../api/bookings'
import { formatTimeRange } from '../lib/dates'
import { EventDetails } from './EventDetails'

interface Props {
  event: BookingEvent
  policy: BookingPolicy | null
  onChanged: () => Promise<void> | void
}

/**
 * One booking in the day panel. Private ones deliberately show no title — only
 * when the room is taken and who to ask about it, which is what the calendar is
 * for.
 *
 * The hours step aside while the edit form is open: the form's own pickers say
 * when the booking is, and a second, stale copy of it above them would only
 * invite the question of which one is being changed.
 */
export function EventCard({ event, policy, onChanged }: Props) {
  const [editing, setEditing] = useState(false)

  return (
    <article className={`event event--${event.category}`}>
      {!editing && <div className="event__when">{formatTimeRange(event.start, event.end)}</div>}
      <h3 className="event__title">
        {event.category === 'public' ? event.title : 'Privat booking'}
      </h3>
      <EventDetails
        event={event}
        policy={policy}
        onChanged={onChanged}
        onEditingChange={setEditing}
      />
    </article>
  )
}
