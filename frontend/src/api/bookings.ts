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
}

export interface BookingPolicy {
  private_bookings_enabled: boolean
  /** A private booking must be at least this many days ahead. */
  private_booking_min_notice_days: number
  /** And at most this many. */
  private_booking_max_horizon_days: number
  /** Weekdays a private booking may start on. 0 = Monday. */
  private_booking_weekdays: number[]
}

export interface NewBooking {
  category: EventCategory
  title?: string
  description?: string
  start: string
  end: string
}

export interface NewSeries {
  title: string
  description?: string
  frequency: Frequency
  interval: number
  until: string
  start: string
  end: string
}

export const bookings = {
  list: (from: string, to: string) =>
    api.get<BookingEvent[]>(`/bookings/events/?from=${from}&to=${to}`),
  create: (booking: NewBooking) => api.post<BookingEvent>('/bookings/events/', booking),
  cancel: (id: number) => api.post<BookingEvent>(`/bookings/events/${id}/cancel/`),
  attend: (id: number) => api.post<BookingEvent>(`/bookings/events/${id}/attendance/`),
  withdraw: (id: number) => api.delete<void>(`/bookings/events/${id}/attendance/`),
  createSeries: (series: NewSeries) =>
    api.post<{ id: number; occurrences: BookingEvent[] }>('/bookings/series/', series),
  policy: () => api.get<BookingPolicy>('/bookings/settings/'),
}
