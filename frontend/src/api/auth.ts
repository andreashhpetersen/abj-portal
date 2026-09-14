/**
 * Public signup.
 *
 * The session endpoints (csrf, login, logout, me) are driven from
 * `auth/AuthContext`, because they change who is logged in. Signup does not:
 * it creates an inactive account for the board to approve, so it is an
 * ordinary API call with no bearing on the current session.
 */

import { api } from './client'

export interface SignupPayload {
  email: string
  first_name: string
  last_name: string
  /** Optional for the resident; the API wants the key regardless. */
  phone: string
  /** Free text, as the applicant writes it — the board matches it by hand. */
  address: string
  /** Optional, and deliberately not format-checked here either. */
  resident_number: string
  password: string
  /**
   * Honeypot. A human leaves it empty and the server silently drops anything
   * that does not — see `SignupSerializer`. Sent as part of the payload so the
   * field is real; it is hidden in the form, not here.
   */
  website: string
}

/** What the server answers every signup with, whatever it actually did. */
export interface SignupReceipt {
  detail: string
}

export const auth = {
  /**
   * Always resolves to the same receipt — created, email already known, or
   * honeypot filled. The frontend cannot tell them apart, which is the point:
   * distinguishing them would turn the form into a way to ask who lives here.
   */
  signup: async (payload: SignupPayload): Promise<SignupReceipt> => {
    // Signup is the one unsafe request an anonymous visitor makes, and it can
    // be the first request of the visit, so the CSRF cookie may not exist yet.
    await api.ensureCsrf()
    return api.post<SignupReceipt>('/auth/signup/', payload)
  },
}
