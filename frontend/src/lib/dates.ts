/**
 * Date helpers for the calendar.
 *
 * Everything here works in the browser's local time. Note that `dateKey` builds
 * its string by hand rather than using `toISOString().slice(0, 10)` — that would
 * be UTC, so an evening booking in Copenhagen would land on the wrong day.
 */

/** Danish weeks start on Monday. */
export const WEEKDAYS = ['man', 'tir', 'ons', 'tor', 'fre', 'lør', 'søn']

/** Full names, in the same Monday-first order the API uses (0 = Monday). */
export const WEEKDAY_NAMES = [
  'mandag',
  'tirsdag',
  'onsdag',
  'torsdag',
  'fredag',
  'lørdag',
  'søndag',
]

/** Monday-first weekday of a day key, matching Python's date.weekday(). */
export function weekdayOf(day: string): number {
  const [year, month, date] = day.split('-').map(Number)
  return (new Date(year, month - 1, date).getDay() + 6) % 7
}

/** Whole calendar days from today to that day key — the unit the booking
 *  window is measured in, so an afternoon booking for a morning slot two weeks
 *  out is not refused on a technicality. */
export function daysFromToday(day: string): number {
  const [year, month, date] = day.split('-').map(Number)
  const target = new Date(year, month - 1, date)
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.round((target.getTime() - today.getTime()) / 86_400_000)
}

/**
 * Every half hour of the day as "HH:mm", 00:00 to 23:30.
 *
 * The booking form picks from these rather than using <input type="time">,
 * which renders as a 12-hour AM/PM control whenever the *browser's* locale is
 * English — the document's lang attribute does not override that.
 */
export const TIME_SLOTS = Array.from({ length: 48 }, (_, index) => {
  const hours = String(Math.floor(index / 2)).padStart(2, '0')
  return `${hours}:${index % 2 === 0 ? '00' : '30'}`
})

/**
 * ISO 8601 week number — the numbering Denmark uses, so "uge 34" in the
 * calendar matches every other Danish calendar.
 *
 * A week belongs to the year that holds its Thursday, so the count runs from
 * this week's Thursday to the Thursday of week 1 (the week containing 4
 * January) rather than from the date itself. That is what makes 1 January
 * correctly read as week 52 or 53 of the previous year.
 */
export function isoWeek(date: Date): number {
  const thursday = thursdayOfWeek(date)
  const firstThursday = thursdayOfWeek(new Date(thursday.getFullYear(), 0, 4))
  const weeks = (thursday.getTime() - firstThursday.getTime()) / (7 * 86_400_000)
  // Round: the difference is a whole number of weeks give or take a DST hour.
  return 1 + Math.round(weeks)
}

function thursdayOfWeek(date: Date): Date {
  const mondayOffset = (date.getDay() + 6) % 7
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + 3 - mondayOffset)
}

/** Local YYYY-MM-DD, the key everything in the calendar is grouped by. */
export function dateKey(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

/**
 * Local HH:mm of an instant — `dateKey`'s counterpart, and hand-built for the
 * same reason. It is what the edit form fills its time pickers from, so it must
 * be the wall-clock time the booking shows on the calendar, not a UTC one.
 */
export function timeKey(iso: string): string {
  const date = new Date(iso)
  const hours = String(date.getHours()).padStart(2, '0')
  return `${hours}:${String(date.getMinutes()).padStart(2, '0')}`
}

export function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1)
}

export function addMonths(date: Date, count: number): Date {
  return new Date(date.getFullYear(), date.getMonth() + count, 1)
}

/** Six Monday-first weeks covering the month, including the padding days. */
export function monthGrid(month: Date): Date[] {
  const first = startOfMonth(month)
  const mondayOffset = (first.getDay() + 6) % 7
  return Array.from(
    { length: 42 },
    (_, index) => new Date(first.getFullYear(), first.getMonth(), 1 - mondayOffset + index),
  )
}

/** Every local day an event touches, so a party past midnight shows on both. */
export function daysCovered(startIso: string, endIso: string): string[] {
  const start = new Date(startIso)
  const end = new Date(endIso)
  const days: string[] = []
  const cursor = new Date(start.getFullYear(), start.getMonth(), start.getDate())

  while (cursor < end) {
    days.push(dateKey(cursor))
    cursor.setDate(cursor.getDate() + 1)
  }
  return days.length > 0 ? days : [dateKey(start)]
}

/** Turn a day key and a HH:mm time into an instant the API will accept. */
export function combineLocal(day: string, time: string, dayOffset = 0): string {
  const [year, month, date] = day.split('-').map(Number)
  const [hours, minutes] = time.split(':').map(Number)
  return new Date(year, month - 1, date + dayOffset, hours, minutes).toISOString()
}

export function formatTime(iso: string): string {
  // hour12: false is belt and braces — da-DK is already a 24-hour locale.
  return new Date(iso).toLocaleTimeString('da-DK', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

export function formatTimeRange(startIso: string, endIso: string): string {
  const sameDay = daysCovered(startIso, endIso).length === 1
  const end = formatTime(endIso)
  return `${formatTime(startIso)}–${end}${sameDay ? '' : ' (næste dag)'}`
}

/** A date on one line, e.g. "20. aug. 2026". For lists, where the time of day
 *  an application arrived is noise. */
export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('da-DK', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

/** Whole days since an instant, counted in local calendar days like
 *  `daysFromToday` — so "i gang i 47 dage" does not tick over at an hour that
 *  depends on when the status happened to change. */
export function daysSince(iso: string): number {
  const then = new Date(iso)
  const start = new Date(then.getFullYear(), then.getMonth(), then.getDate())
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.round((today.getTime() - start.getTime()) / 86_400_000)
}

export function formatDayLong(date: Date): string {
  return date.toLocaleDateString('da-DK', { weekday: 'long', day: 'numeric', month: 'long' })
}

export function formatMonth(date: Date): string {
  return date.toLocaleDateString('da-DK', { month: 'long', year: 'numeric' })
}

export function isSameMonth(date: Date, month: Date): boolean {
  return date.getMonth() === month.getMonth() && date.getFullYear() === month.getFullYear()
}
