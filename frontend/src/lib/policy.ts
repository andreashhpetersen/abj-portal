/**
 * The private-booking policy, mirrored on the client.
 *
 * The server is the authority — `Event._private_booking_errors` in
 * `backend/apps/bookings/models.py` — but a form that says up front why a day
 * is closed beats one that waits for a rejected submit. Both the booking form
 * and the edit form need the same answer, so they ask it here rather than
 * each keeping its own copy of the rules.
 */

import type { BookingPolicy } from '../api/bookings'

import { WEEKDAY_NAMES, daysFromToday, weekdayOf } from './dates'

/**
 * Why a private booking cannot be placed on that day, or null if it can.
 *
 * `exempt` is for admins, who are bound by none of this. A null policy means
 * it has not loaded yet — say nothing rather than guess.
 */
export function privateBookingProblem(
  day: string,
  policy: BookingPolicy | null,
  exempt: boolean,
): string | null {
  if (policy === null || exempt) return null

  if (!policy.private_bookings_enabled) {
    return 'Private bookinger er lukket for øjeblikket.'
  }

  const daysAhead = daysFromToday(day)
  if (daysAhead < policy.private_booking_min_notice_days) {
    return `Private bookinger skal laves mindst ${policy.private_booking_min_notice_days} dage frem.`
  }
  if (daysAhead > policy.private_booking_max_horizon_days) {
    return `Private bookinger kan laves højst ${policy.private_booking_max_horizon_days} dage frem.`
  }
  if (!policy.private_booking_weekdays.includes(weekdayOf(day))) {
    const open = policy.private_booking_weekdays
      .map((weekday) => WEEKDAY_NAMES[weekday])
      .join(', ')
    return `Lokalet kan kun bookes privat på: ${open}.`
  }
  return null
}
