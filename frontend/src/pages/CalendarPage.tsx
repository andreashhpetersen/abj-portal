/**
 * Feature 1: the community-room calendar.
 *
 * A month grid plus a panel for the selected day. Events are fetched for the
 * whole visible grid — including the padding days from neighbouring months, so
 * a booking on the 31st does not vanish when it sits in the previous month's
 * last row.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import type { BookingEvent, BookingPolicy } from '../api/bookings'
import { bookings } from '../api/bookings'
import { BookingForm } from '../components/BookingForm'
import { EventCard } from '../components/EventCard'
import { MonthGrid } from '../components/MonthGrid'
import { dateKey, daysCovered, formatDayLong, monthGrid, startOfMonth } from '../lib/dates'

export function CalendarPage() {
  const [month, setMonth] = useState(() => startOfMonth(new Date()))
  const [selected, setSelected] = useState(() => dateKey(new Date()))
  const [events, setEvents] = useState<BookingEvent[]>([])
  const [policy, setPolicy] = useState<BookingPolicy | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const visibleRange = useMemo(() => {
    const grid = monthGrid(month)
    return { from: dateKey(grid[0]), to: dateKey(grid[grid.length - 1]) }
  }, [month])

  const reload = useCallback(async () => {
    setError(null)
    try {
      setEvents(await bookings.list(visibleRange.from, visibleRange.to))
    } catch {
      setError('Kalenderen kunne ikke hentes.')
    }
  }, [visibleRange.from, visibleRange.to])

  useEffect(() => {
    let cancelled = false

    async function load() {
      setLoading(true)
      try {
        const [loaded, loadedPolicy] = await Promise.all([
          bookings.list(visibleRange.from, visibleRange.to),
          bookings.policy(),
        ])
        if (!cancelled) {
          setEvents(loaded)
          setPolicy(loadedPolicy)
          setError(null)
        }
      } catch {
        if (!cancelled) setError('Kalenderen kunne ikke hentes.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [visibleRange.from, visibleRange.to])

  const eventsByDay = useMemo(() => {
    const grouped = new Map<string, BookingEvent[]>()
    for (const event of events) {
      for (const day of daysCovered(event.start, event.end)) {
        grouped.set(day, [...(grouped.get(day) ?? []), event])
      }
    }
    for (const list of grouped.values()) {
      list.sort((left, right) => left.start.localeCompare(right.start))
    }
    return grouped
  }, [events])

  const selectedDate = useMemo(() => {
    const [year, monthNumber, day] = selected.split('-').map(Number)
    return new Date(year, monthNumber - 1, day)
  }, [selected])

  const selectedEvents = eventsByDay.get(selected) ?? []

  return (
    <div className="calendar-page">
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      <MonthGrid
        month={month}
        selected={selected}
        eventsByDay={eventsByDay}
        onSelect={setSelected}
        onChangeMonth={setMonth}
      />

      <aside className="day-panel card">
        <h2 className="day-panel__title">{formatDayLong(selectedDate)}</h2>

        {loading ? (
          <p className="status">Indlæser…</p>
        ) : selectedEvents.length === 0 ? (
          <p className="day-panel__empty">Ingen bookinger denne dag.</p>
        ) : (
          <div className="day-panel__events">
            {selectedEvents.map((event) => (
              <EventCard key={event.id} event={event} policy={policy} onChanged={reload} />
            ))}
          </div>
        )}

        <BookingForm day={selected} policy={policy} onCreated={reload} />
      </aside>
    </div>
  )
}
