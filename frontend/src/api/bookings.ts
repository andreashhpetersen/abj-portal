/** Typed access to /api/bookings/. Mirrors the DRF serializers. */

import { api } from './client'

export type EventCategory = 'private' | 'public'
export type Frequency = 'daily' | 'weekly' | 'monthly'

/** Who booked, and how to reach them — shown on the calendar. */
export interface Contact {
  id: number
  name: string
  email: string
  phone: string
}

export interface BookingEvent {
  id: number
  category: EventCategory
  title: string
  description: string
  start: string
  end: string
  created_by: Contact
  series: number | null
  is_cancelled: boolean
  cancelled_at: string | null
  attendee_count: number
  is_attending: boolean
  /** Whether the signed-in user may cancel this one. Decided by the server. */
  can_cancel: boolean
  /** And whether they may change it. Cancelled bookings are a record, not a
   *  plan, so neither is true of one. */
  can_edit: boolean
}

export interface BookingPolicy {
  private_bookings_enabled: boolean
  /** A private booking must be at least this many days ahead. */
  private_booking_min_notice_days: number
  /** And at most this many. */
  private_booking_max_horizon_days: number
  /** Weekdays a private booking may start on. 0 = Monday. */
  private_booking_weekdays: number[]
  /** When false, only the beboerlokaleudvalg and admins may create a public
   *  booking. Booking one in someone else's name stays restricted to them
   *  either way. */
  public_bookings_open: boolean
}

/** Someone a privileged booker can name as the organizer of a public event. */
export interface OrganizerCandidate {
  id: number
  name: string
  email: string
}

export interface NewBooking {
  category: EventCategory
  title?: string
  description?: string
  start: string
  end: string
  /** Only honoured for a privileged booker naming someone other than
   *  themselves — the server ignores it otherwise. */
  organizer_id?: number
}

/** What an edit may change. The category is not among them: a private booking
 *  and a public event are different things, booked afresh rather than
 *  converted. */
export interface BookingChanges {
  title?: string
  description?: string
  start: string
  end: string
}

/** A series as the API reports it. `occurrences` comes too, but nothing here
 *  needs them — the calendar has already fetched the ones it shows. */
export interface EventSeriesDetail {
  id: number
  title: string
  description: string
  frequency: Frequency
  interval: number
  /** Last date an occurrence may fall on. */
  until: string
  created_by: Contact
}

/**
 * What an edit may change about a whole series.
 *
 * The times move by a delta rather than to an absolute hour, which is what
 * leaves an occurrence somebody moved by hand still moved. The rhythm —
 * `frequency` and `interval` — is not here: a different rhythm is a different
 * series, and changing it means deleting this one and making another.
 */
export interface SeriesChanges {
  title?: string
  description?: string
  until?: string
  start_shift_minutes?: number
  end_shift_minutes?: number
}

export interface NewSeries {
  title: string
  description?: string
  frequency: Frequency
  interval: number
  until: string
  start: string
  end: string
  /** Only honoured for a privileged booker naming someone other than
   *  themselves — the server ignores it otherwise. */
  organizer_id?: number
}

export const bookings = {
  list: (from: string, to: string) =>
    api.get<BookingEvent[]>(`/bookings/events/?from=${from}&to=${to}`),
  create: (booking: NewBooking) => api.post<BookingEvent>('/bookings/events/', booking),
  update: (id: number, changes: BookingChanges) =>
    api.patch<BookingEvent>(`/bookings/events/${id}/`, changes),
  cancel: (id: number) => api.post<BookingEvent>(`/bookings/events/${id}/cancel/`),
  attend: (id: number) => api.post<BookingEvent>(`/bookings/events/${id}/attendance/`),
  withdraw: (id: number) => api.delete<void>(`/bookings/events/${id}/attendance/`),
  createSeries: (series: NewSeries) =>
    api.post<{ id: number; occurrences: BookingEvent[] }>('/bookings/series/', series),
  series: (id: number) => api.get<EventSeriesDetail>(`/bookings/series/${id}/`),
  updateSeries: (id: number, changes: SeriesChanges) =>
    api.patch<EventSeriesDetail>(`/bookings/series/${id}/`, changes),
  policy: () => api.get<BookingPolicy>('/bookings/settings/'),
  /** Who a privileged booker may name as the organizer of a public event.
   *  403s for anyone else — the form only calls it once it knows. */
  organizers: () => api.get<OrganizerCandidate[]>('/bookings/organizers/'),
}
