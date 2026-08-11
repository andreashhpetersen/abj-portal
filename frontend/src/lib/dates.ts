/**
 * Date helpers for the calendar.
 *
 * Everything here works in the browser's local time. Note that `dateKey` builds
 * its string by hand rather than using `toISOString().slice(0, 10)` — that would
 * be UTC, so an evening booking in Copenhagen would land on the wrong day.
 */

/** Danish weeks start on Monday. */
export const WEEKDAYS = ['man', 'tir', 'ons', 'tor', 'fre', 'lør', 'søn']

/** Local YYYY-MM-DD, the key everything in the calendar is grouped by. */
export function dateKey(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
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
  return new Date(iso).toLocaleTimeString('da-DK', { hour: '2-digit', minute: '2-digit' })
}

export function formatTimeRange(startIso: string, endIso: string): string {
  const sameDay = daysCovered(startIso, endIso).length === 1
  const end = formatTime(endIso)
  return `${formatTime(startIso)}–${end}${sameDay ? '' : ' (næste dag)'}`
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
