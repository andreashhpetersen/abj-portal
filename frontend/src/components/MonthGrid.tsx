import { Fragment } from 'react'
import type { BookingEvent } from '../api/bookings'
import { WEEKDAYS, dateKey, formatMonth, isSameMonth, isoWeek, monthGrid } from '../lib/dates'

interface Props {
  month: Date
  selected: string
  eventsByDay: Map<string, BookingEvent[]>
  onSelect: (day: string) => void
}

/**
 * The month view. Each day shows a dot per booking rather than the bookings
 * themselves — the detail belongs in the day panel, and dots survive a narrow
 * phone screen where text would not.
 *
 * Which month is on show, and the buttons that change it, live in `MonthNav`
 * above: the grid and the event list are two readings of one month.
 */
export function MonthGrid({ month, selected, eventsByDay, onSelect }: Props) {
  const today = dateKey(new Date())
  const days = monthGrid(month)

  return (
    <>
      <div className="calendar__weekdays" aria-hidden="true">
        <span className="calendar__week-heading">uge</span>
        {WEEKDAYS.map((weekday) => (
          <span key={weekday}>{weekday}</span>
        ))}
      </div>

      <div className="calendar__grid">
        {days.map((day, index) => {
          const key = dateKey(day)
          const dayEvents = eventsByDay.get(key) ?? []
          const classes = [
            'calendar__day',
            isSameMonth(day, month) ? '' : 'calendar__day--outside',
            key === today ? 'calendar__day--today' : '',
            key === selected ? 'calendar__day--selected' : '',
          ]
            .filter(Boolean)
            .join(' ')

          return (
            <Fragment key={key}>
              {/* The grid is a flat list of cells, so each Monday is preceded by
                  the week number that labels its row. */}
              {index % 7 === 0 && (
                <span className="calendar__week" title={`Uge ${isoWeek(day)}`}>
                  {isoWeek(day)}
                </span>
              )}
              <button
                type="button"
                className={classes}
                onClick={() => onSelect(key)}
                aria-pressed={key === selected}
                aria-label={`${day.getDate()}. ${formatMonth(day)}, uge ${isoWeek(day)}, ${dayEvents.length} booking(er)`}
              >
                <span className="calendar__date">{day.getDate()}</span>
                <span className="calendar__dots">
                  {dayEvents.slice(0, 3).map((event) => (
                    <span
                      key={event.id}
                      className={`dot dot--${event.category}`}
                      title={event.category === 'public' ? event.title : 'Privat booking'}
                    />
                  ))}
                  {dayEvents.length > 3 && (
                    <span className="calendar__more">+{dayEvents.length - 3}</span>
                  )}
                </span>
              </button>
            </Fragment>
          )
        })}
      </div>

      <p className="calendar__legend">
        <span className="dot dot--public" /> Fælles arrangement
        <span className="dot dot--private" /> Privat booking
      </p>
    </>
  )
}
