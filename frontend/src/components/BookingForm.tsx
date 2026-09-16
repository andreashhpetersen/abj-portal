import { useState } from 'react'

import type { BookingPolicy, EventCategory, Frequency } from '../api/bookings'
import { bookings } from '../api/bookings'
import { fieldErrors } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { TIME_SLOTS, combineLocal } from '../lib/dates'
import { privateBookingProblem } from '../lib/policy'

import type { FormEvent } from 'react'

interface Props {
  day: string
  policy: BookingPolicy | null
  onCreated: () => Promise<void> | void
}

const FREQUENCY_LABELS: Record<Frequency, string> = {
  daily: 'Hver dag',
  weekly: 'Hver uge',
  monthly: 'Hver måned',
}

export function BookingForm({ day, policy, onCreated }: Props) {
  const { member } = useAuth()
  const [category, setCategory] = useState<EventCategory>('private')
  const [startTime, setStartTime] = useState('18:00')
  const [endTime, setEndTime] = useState('23:00')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [repeats, setRepeats] = useState(false)
  const [frequency, setFrequency] = useState<Frequency>('weekly')
  const [interval, setInterval] = useState(1)
  const [until, setUntil] = useState(day)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)

  // Admins may book privately whatever the policy says, so the form follows the
  // same rules the server enforces rather than guessing.
  const exempt = member?.is_staff ?? false
  const privateClosed = policy !== null && !policy.private_bookings_enabled && !exempt
  const isPublic = category === 'public'
  const privateBlockedReason = privateBookingProblem(day, policy, exempt)
  // A booking that ends earlier than it starts runs past midnight.
  const endsNextDay = endTime <= startTime

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setErrors({})
    setSubmitting(true)

    const start = combineLocal(day, startTime)
    const end = combineLocal(day, endTime, endsNextDay ? 1 : 0)

    try {
      if (isPublic && repeats) {
        await bookings.createSeries({
          title,
          description,
          frequency,
          interval,
          until,
          start,
          end,
        })
      } else {
        await bookings.create({
          category,
          title: isPublic ? title : '',
          description: isPublic ? description : '',
          start,
          end,
        })
      }
      setTitle('')
      setDescription('')
      setRepeats(false)
      await onCreated()
    } catch (caught) {
      const reported = fieldErrors(caught)
      setErrors(
        Object.keys(reported).length > 0
          ? reported
          : { detail: 'Bookingen kunne ikke oprettes. Prøv igen.' },
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form className="booking-form" onSubmit={handleSubmit}>
      <h3>Book lokalet</h3>

      <fieldset className="booking-form__categories">
        <legend>Type</legend>
        <label>
          <input
            type="radio"
            name="category"
            checked={!isPublic}
            disabled={privateClosed}
            onChange={() => setCategory('private')}
          />
          Privat
        </label>
        <label>
          <input
            type="radio"
            name="category"
            checked={isPublic}
            onChange={() => setCategory('public')}
          />
          Fælles arrangement
        </label>
      </fieldset>

      {privateClosed && <p className="hint">Private bookinger er lukket for øjeblikket.</p>}
      {!isPublic && !privateClosed && privateBlockedReason && (
        <p className="hint hint--blocking">{privateBlockedReason}</p>
      )}
      {!isPublic && !privateClosed && !privateBlockedReason && policy && (
        <p className="hint">
          Private bookinger skal laves mindst {policy.private_booking_min_notice_days} og højst{' '}
          {policy.private_booking_max_horizon_days} dage frem.
        </p>
      )}
      {errors.category && <p className="error">{errors.category}</p>}

      <div className="booking-form__times">
        <label>
          Fra
          <select value={startTime} onChange={(changed) => setStartTime(changed.target.value)}>
            {TIME_SLOTS.map((slot) => (
              <option key={slot} value={slot}>
                {slot}
              </option>
            ))}
          </select>
        </label>
        <label>
          Til
          <select value={endTime} onChange={(changed) => setEndTime(changed.target.value)}>
            {TIME_SLOTS.map((slot) => (
              <option key={slot} value={slot}>
                {slot}
              </option>
            ))}
          </select>
        </label>
      </div>
      {endsNextDay && <p className="hint">Bookingen slutter dagen efter.</p>}
      {errors.start && <p className="error">{errors.start}</p>}
      {errors.end && <p className="error">{errors.end}</p>}

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

          <label className="booking-form__checkbox">
            <input
              type="checkbox"
              checked={repeats}
              onChange={(changed) => setRepeats(changed.target.checked)}
            />
            Gentages
          </label>

          {repeats && (
            <div className="booking-form__repeat">
              <label>
                Hvor ofte
                <select
                  value={frequency}
                  onChange={(changed) => setFrequency(changed.target.value as Frequency)}
                >
                  {Object.entries(FREQUENCY_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Interval
                <input
                  type="number"
                  min={1}
                  max={12}
                  value={interval}
                  onChange={(changed) => setInterval(Number(changed.target.value))}
                />
              </label>
              <label>
                Til og med
                <input
                  type="date"
                  value={until}
                  min={day}
                  required
                  onChange={(changed) => setUntil(changed.target.value)}
                />
              </label>
              {errors.until && <p className="error">{errors.until}</p>}
            </div>
          )}
        </>
      )}

      {errors.detail && (
        <p role="alert" className="error">
          {errors.detail}
        </p>
      )}

      <button
        type="submit"
        disabled={submitting || (!isPublic && (privateClosed || privateBlockedReason !== null))}
      >
        {submitting ? 'Booker…' : 'Book'}
      </button>
    </form>
  )
}
