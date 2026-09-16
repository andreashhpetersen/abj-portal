import type { BookingEvent } from '../api/bookings'
import { formatTimeRange } from '../lib/dates'
import { EventDetails } from './EventDetails'

interface Props {
  event: BookingEvent
  onChanged: () => Promise<void> | void
}

/**
 * One booking in the day panel. Private ones deliberately show no title — only
 * when the room is taken and who to ask about it, which is what the calendar is
 * for.
 */
export function EventCard({ event, onChanged }: Props) {
  return (
    <article className={`event event--${event.category}`}>
      <div className="event__when">{formatTimeRange(event.start, event.end)}</div>
      <h3 className="event__title">
        {event.category === 'public' ? event.title : 'Privat booking'}
      </h3>
      <EventDetails event={event} onChanged={onChanged} />
    </article>
  )
}
