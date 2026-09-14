/**
 * "I forgot my password" — the request half of the reset flow.
 *
 * Submitting always lands on the same receipt, whether or not the address
 * has an account: the server answers identically either way (see
 * `PasswordResetRequestView`), and this page must not try to be more
 * informative than that.
 */

import { useState } from 'react'
import { Link, Navigate } from 'react-router-dom'

import { auth } from '../api/auth'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'

import type { FormEvent } from 'react'

const THROTTLED =
  'Der er sendt for mange anmodninger fra dette netværk. Prøv igen om en times tid, ' +
  'eller kontakt bestyrelsen.'

export function ForgotPasswordPage() {
  const { member, loading } = useAuth()
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [receipt, setReceipt] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (loading) return <p className="status">Indlæser…</p>
  if (member) return <Navigate to="/" replace />

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const { detail } = await auth.requestPasswordReset(email)
      setReceipt(detail)
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 429
          ? THROTTLED
          : 'Anmodningen kunne ikke sendes. Prøv igen.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  if (receipt !== null) {
    return (
      <div className="login">
        <div className="card login__form">
          <h1>Tjek din indbakke</h1>
          <p>{receipt}</p>
          <Link to="/login">Tilbage til login</Link>
        </div>
      </div>
    )
  }

  return (
    <div className="login">
      <form className="card login__form" onSubmit={handleSubmit}>
        <h1>Glemt adgangskode</h1>
        <label htmlFor="email">
          Email
          <input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Sender…' : 'Send link'}
        </button>
        <p className="hint">
          <Link to="/login">Tilbage til login</Link>
        </p>
      </form>
    </div>
  )
}
