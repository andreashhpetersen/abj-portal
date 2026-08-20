/**
 * Typed access to /api/shop-rentals/. Mirrors the DRF serializers.
 *
 * Everything here is erhvervsudvalg-only. The route is gated in App.tsx as a
 * convenience; the real boundary is IsBusinessCommittee on the server, so a
 * non-member calling these gets a 403 rather than data.
 */

import { api } from './client'

import type { Contact } from './bookings'

/** The committee's five working states. Mirrors ApplicationStatus. */
export type ApplicationStatus = 'new' | 'in_progress' | 'saved' | 'rejected' | 'finished'

/** In the order the committee reads them: newest first, then the two piles
 *  that are still live, then the two that are done with. */
export const STATUS_ORDER: ApplicationStatus[] = [
  'new',
  'in_progress',
  'saved',
  'rejected',
  'finished',
]

/** Danish labels. The server sends `status_display` too, but the filter chips
 *  need a label for statuses that currently have no applications. */
export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  new: 'Ny',
  in_progress: 'I gang',
  saved: 'Gemt til senere',
  rejected: 'Afvist',
  finished: 'Afsluttet',
}

/** One question and its answer, exactly as the form recorded it. */
export interface Answer {
  question: string
  value: string
}

export interface CommitteeMember {
  id: number
  name: string
  email: string
}

export interface Application {
  id: number
  submitted_at: string
  applicant_name: string
  email: string
  phone: string
  /** Every answer, in the form's own order. The form changes, so the UI
   *  renders whatever arrives rather than naming fields it expects. */
  answers: Answer[]
  status: ApplicationStatus
  status_display: string
  status_changed_at: string | null
  /** 1–5, or null for not yet rated — which is not the same as rated 1. */
  rating: number | null
  assignee: CommitteeMember | null
  comment_count: number
  has_details: boolean
  synced_at: string | null
}

export interface ApplicationComment {
  id: number
  body: string
  author: Contact
  created_at: string
  /** Whether the signed-in user may delete this one. Decided by the server. */
  can_delete: boolean
}

/** The facts gathered after making contact. Every field optional — a
 *  half-filled record is the normal state, not an error. */
export interface ContractDetails {
  company_name: string
  cvr: string
  legal_form: string
  company_address: string
  contact_name: string
  contact_email: string
  contact_phone: string
  unit_label: string
  purpose: string
  area_sqm: string | null
  annual_rent_dkk: string | null
  deposit_months: number | null
  lease_start: string | null
  notes: string
  updated_at: string
  /** Danish labels of what the lawyer still needs. */
  missing_fields: string[]
  is_complete: boolean
}

/**
 * The answers worth showing below the header, i.e. all but the three the header
 * already shows.
 *
 * Matched on the value rather than the question, because the question's wording
 * is the thing that changes. The upshot is the right one either way: when the
 * form's email question is recognised, the header shows it and this drops the
 * duplicate; when a rewording leaves `email` empty, nothing matches and the
 * answer stays visible in the list where it can still be read.
 */
export function bodyAnswers(application: Application): Answer[] {
  const alreadyShown = [
    application.applicant_name,
    application.email,
    application.phone,
  ].filter(Boolean)
  return application.answers.filter((answer) => !alreadyShown.includes(answer.value))
}

export interface StatusSummary {
  total: number
  by_status: Record<ApplicationStatus, number>
}

export interface Page<T> {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}

export interface ApplicationFilters {
  /** Repeatable, so the chips can be multi-select. Empty means every status. */
  status?: ApplicationStatus[]
  /** A member id, or 'unassigned' for the pile nobody has picked up. */
  assignee?: string
  minRating?: number
  q?: string
  ordering?: string
  page?: number
}

function queryString(filters: ApplicationFilters): string {
  const params = new URLSearchParams()
  for (const status of filters.status ?? []) params.append('status', status)
  if (filters.assignee) params.set('assignee', filters.assignee)
  if (filters.minRating) params.set('min_rating', String(filters.minRating))
  if (filters.q) params.set('q', filters.q)
  if (filters.ordering) params.set('ordering', filters.ordering)
  if (filters.page && filters.page > 1) params.set('page', String(filters.page))
  const query = params.toString()
  return query ? `?${query}` : ''
}

const BASE = '/shop-rentals'

export const shopRentals = {
  list: (filters: ApplicationFilters = {}) =>
    api.get<Page<Application>>(`${BASE}/applications/${queryString(filters)}`),
  get: (id: number) => api.get<Application>(`${BASE}/applications/${id}/`),
  summary: () => api.get<StatusSummary>(`${BASE}/applications/summary/`),
  /** Only status, rating and assignee are writable — the applicant's answers
   *  are a record of what was submitted. */
  update: (
    id: number,
    changes: Partial<{
      status: ApplicationStatus
      rating: number | null
      assignee_id: number | null
    }>,
  ) => api.patch<Application>(`${BASE}/applications/${id}/`, changes),
  comments: (id: number) => api.get<ApplicationComment[]>(`${BASE}/applications/${id}/comments/`),
  addComment: (id: number, body: string) =>
    api.post<ApplicationComment>(`${BASE}/applications/${id}/comments/`, { body }),
  deleteComment: (commentId: number) => api.delete<void>(`${BASE}/comments/${commentId}/`),
  /** Created server-side on first read, so there is no "does it exist yet". */
  details: (id: number) => api.get<ContractDetails>(`${BASE}/applications/${id}/details/`),
  saveDetails: (id: number, changes: Partial<ContractDetails>) =>
    api.patch<ContractDetails>(`${BASE}/applications/${id}/details/`, changes),
  members: () => api.get<CommitteeMember[]>(`${BASE}/members/`),
}
