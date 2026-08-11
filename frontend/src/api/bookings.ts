/** Typed access to /api/bookings/. Mirrors the DRF serializers. */

import { ApiError, api } from './client'

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
  private_booking_horizon_days: number
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

/**
 * DRF reports validation failures as { field: ["message", ...] }. Flatten that
 * into one message per field so a form can show them next to their inputs.
 */
export function fieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError) || error.status !== 400 || typeof error.data !== 'object') {
    return {}
  }
  const flattened: Record<string, string> = {}
  for (const [field, messages] of Object.entries(error.data as Record<string, unknown>)) {
    flattened[field] = Array.isArray(messages) ? messages.join(' ') : String(messages)
  }
  return flattened
}
