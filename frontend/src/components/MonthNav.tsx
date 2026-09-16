import { addMonths, formatMonth } from '../lib/dates'

interface Props {
  month: Date
  onChangeMonth: (month: Date) => void
}

/**
 * Which month you are looking at, and the two buttons that change it.
 *
 * The grid and the event list are two readings of the same month, so they step
 * through it with one control rather than each keeping its own.
 */
export function MonthNav({ month, onChangeMonth }: Props) {
  return (
    <header className="calendar__header">
      <button
        type="button"
        className="calendar__nav"
        onClick={() => onChangeMonth(addMonths(month, -1))}
        aria-label="Forrige måned"
      >
        ‹
      </button>
      <h2 className="calendar__title">{formatMonth(month)}</h2>
      <button
        type="button"
        className="calendar__nav"
        onClick={() => onChangeMonth(addMonths(month, 1))}
        aria-label="Næste måned"
      >
        ›
      </button>
    </header>
  )
}
