import { useState } from 'react'

import type { BookingEvent, BookingPolicy, EventSeriesDetail } from '../api/bookings'
import { bookings } from '../api/bookings'
import { fieldErrors } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { TIME_SLOTS, combineLocal, dateKey, timeKey } from '../lib/dates'
import { privateBookingProblem } from '../lib/policy'

import type { FormEvent } from 'react'

interface Props {
  event: BookingEvent
  policy: BookingPolicy | null
  onSaved: () => Promise<void> | void
  onCancel: () => void
}

/** Whether the change lands on this booking alone or on the whole series. */
type Scope = 'occurrence' | 'series'

/**
 * Change a booking you made: when it is, and — for a public event — what it
 * says. The category stays put, because turning a private booking into an
 * arrangement for the whole association is a different booking rather than an
 * edit of this one.
 *
 * Moving a private booking to another day is a fresh claim on the room and the
 * server treats it as one, so the same notice period that governs the booking
 * form governs this. Staying on the booked day is not, which is why a booking
 * that has drawn nearer than the notice period can still have its hours fixed.
 *
 * An occurrence of a series can be changed on its own — one week the
 * strikkecafé moves, or is cancelled, and the rest of the term is unaffected —
 * or the change can be applied to every evening still to come. That choice
 * defaults to this one alone, because it is the one that cannot surprise
 * anybody.
 */
export function EventEditForm({ event, policy, onSaved, onCancel }: Props) {
  const { member } = useAuth()
  const bookedDay = dateKey(new Date(event.start))
  const [scope, setScope] = useState<Scope>('occurrence')
  const [series, setSeries] = useState<EventSeriesDetail | null>(null)
  const [loadingSeries, setLoadingSeries] = useState(false)
  const [day, setDay] = useState(bookedDay)
  const [startTime, setStartTime] = useState(() => timeKey(event.start))
  const [endTime, setEndTime] = useState(() => timeKey(event.end))
  const [title, setTitle] = useState(event.title)
  const [description, setDescription] = useState(event.description)
  const [until, setUntil] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  const isPublic = event.category === 'public'
  const belongsToSeries = event.series !== null
  const wholeSeries = scope === 'series'
  const exempt = member?.is_staff ?? false
  // A booking that ends earlier than it starts runs past midnight.
  const endsNextDay = endTime <= startTime
  const movedToAnotherDay = day !== bookedDay
  const blockedReason =
    isPublic || !movedToAnotherDay ? null : privateBookingProblem(day, policy, exempt)

  /**
   * The series owns its own title and dates, and they need not match the
   * occurrence the form was opened on — somebody may have retitled this one
   * evening. So each scope fills the fields from whatever it is about to write.
   */
  async function changeScope(next: Scope) {
    setScope(next)
    setErrors({})
    if (next === 'occurrence') {
      setDay(bookedDay)
      setTitle(event.title)
      setDescription(event.description)
      return
    }
    setDay(bookedDay)
    if (series !== null) {
      fillFromSeries(series)
      return
    }
    setLoadingSeries(true)
    try {
      const loaded = await bookings.series(event.series as number)
      setSeries(loaded)
      fillFromSeries(loaded)
    } catch {
      setErrors({ detail: 'Serien kunne ikke hentes. Prøv igen.' })
      setScope('occurrence')
    } finally {
      setLoadingSeries(false)
    }
  }

  function fillFromSeries(loaded: EventSeriesDetail) {
    setTitle(loaded.title)
    setDescription(loaded.description)
    setUntil(loaded.until)
  }

  async function handleSubmit(submitted: FormEvent) {
    submitted.preventDefault()
    setErrors({})
    setSaving(true)

    const start = combineLocal(day, startTime)
    const end = combineLocal(day, endTime, endsNextDay ? 1 : 0)

    try {
      if (wholeSeries) {
        await bookings.updateSeries(event.series as number, {
          title,
          description,
          until,
          start_shift_minutes: minutesBetween(event.start, start),
          end_shift_minutes: minutesBetween(event.end, end),
        })
      } else {
        await bookings.update(event.id, {
          start,
          end,
          ...(isPublic ? { title, description } : {}),
        })
      }
      await onSaved()
    } catch (caught) {
      const reported = fieldErrors(caught)
      setErrors(
        Object.keys(reported).length > 0
          ? reported
          : { detail: 'Ændringen kunne ikke gemmes. Prøv igen.' },
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <form className="booking-form booking-form--edit" onSubmit={handleSubmit}>
      {belongsToSeries && (
        <fieldset className="booking-form__categories">
          <legend>Ændringen gælder</legend>
          <label>
            <input
              type="radio"
              name={`scope-${event.id}`}
              checked={!wholeSeries}
              onChange={() => void changeScope('occurrence')}
            />
            Kun denne gang
          </label>
          <label>
            <input
              type="radio"
              name={`scope-${event.id}`}
              checked={wholeSeries}
              onChange={() => void changeScope('series')}
            />
            Alle fremtidige gange
          </label>
        </fieldset>
      )}

      {wholeSeries ? (
        <p className="hint">
          Tidligere gange og aflyste gange røres ikke. Vil serien mødes på en anden ugedag, skal der
          oprettes en ny serie.
        </p>
      ) : (
        <label>
          Dato
          <input
            type="date"
            value={day}
            required
            onChange={(changed) => setDay(changed.target.value)}
          />
        </label>
      )}

      <div className="booking-form__times">
        <label>
          Fra
          <select value={startTime} onChange={(changed) => setStartTime(changed.target.value)}>
            {slotsIncluding(startTime).map((slot) => (
              <option key={slot} value={slot}>
                {slot}
              </option>
            ))}
          </select>
        </label>
        <label>
          Til
          <select value={endTime} onChange={(changed) => setEndTime(changed.target.value)}>
            {slotsIncluding(endTime).map((slot) => (
              <option key={slot} value={slot}>
                {slot}
              </option>
            ))}
          </select>
        </label>
      </div>
      {endsNextDay && <p className="hint">Bookingen slutter dagen efter.</p>}
      {blockedReason && <p className="hint hint--blocking">{blockedReason}</p>}
      {errors.start && <p className="error">{errors.start}</p>}
      {errors.end && <p className="error">{errors.end}</p>}
      {errors.category && <p className="error">{errors.category}</p>}
      {errors.start_shift_minutes && <p className="error">{errors.start_shift_minutes}</p>}
      {errors.end_shift_minutes && <p className="error">{errors.end_shift_minutes}</p>}

      {wholeSeries && (
        <>
          <label>
            Til og med
            <input
              type="date"
              value={until}
              required
              onChange={(changed) => setUntil(changed.target.value)}
            />
          </label>
          <p className="hint">
            En senere dato lægger flere gange i kalenderen; en tidligere aflyser dem, der ligger
            efter.
          </p>
          {errors.until && <p className="error">{errors.until}</p>}
        </>
      )}

      {isPublic && (
        <>
          <label>
            Titel
            <input
              type="text"
              value={title}
              required
              maxLength={200}
              onChange={(changed) => setTitle(changed.target.value)}
            />
          </label>
          {errors.title && <p className="error">{errors.title}</p>}

          <label>
            Beskrivelse
            <textarea
              value={description}
              rows={3}
              onChange={(changed) => setDescription(changed.target.value)}
            />
          </label>
        </>
      )}

      {errors.detail && (
        <p role="alert" className="error">
          {errors.detail}
        </p>
      )}

      <div className="event__actions">
        <button type="submit" disabled={saving || loadingSeries || blockedReason !== null}>
          {saving ? 'Gemmer…' : wholeSeries ? 'Gem hele serien' : 'Gem ændringer'}
        </button>
        <button type="button" className="button--quiet" disabled={saving} onClick={onCancel}>
          Fortryd
        </button>
      </div>
      {loadingSeries && <p className="status">Henter serien…</p>}
    </form>
  )
}

/**
 * The half-hour grid, plus the time the booking actually has. A booking entered
 * in the admin can start at 18:45, and a picker that silently rounded it would
 * move the booking the moment somebody opened this form to change its title.
 */
function slotsIncluding(time: string): string[] {
  return TIME_SLOTS.includes(time) ? TIME_SLOTS : [...TIME_SLOTS, time].sort()
}

/** How far the booking moved, which is what a series-wide retime applies to
 *  every other evening. Both instants are on the same day, so this is a
 *  wall-clock difference and no clock change can hide inside it. */
function minutesBetween(from: string, to: string): number {
  return Math.round((new Date(to).getTime() - new Date(from).getTime()) / 60_000)
}
