/**
 * The public signup form — the only page an anonymous visitor can write from.
 *
 * It asks for a login and a claim of residency, not for residency itself: the
 * address and resident number are free text that a board member compares to the
 * association's register by hand, and approval grants a login and nothing more.
 * So no building picker, no format mask on the resident number — the server
 * stores what was typed and a human reads it.
 *
 * Submitting always lands on the same receipt, because the server answers the
 * same 202 whether it created an account, recognised the email, or spotted the
 * honeypot. The page must not try to be more informative than that.
 */

import { useState } from 'react'
import { Link, Navigate } from 'react-router-dom'

import { auth } from '../api/auth'
import { ApiError, fieldErrors } from '../api/client'
import { useAuth } from '../auth/AuthContext'

import type { SignupPayload } from '../api/auth'
import type { FormEvent } from 'react'

const EMPTY: SignupPayload = {
  email: '',
  first_name: '',
  last_name: '',
  phone: '',
  address: '',
  resident_number: '',
  password: '',
  website: '',
}

/** The per-IP signup throttle. Its own message is generic, so we say better. */
const THROTTLED =
  'Der er sendt for mange anmodninger fra dette netværk. Prøv igen om en times tid, ' +
  'eller kontakt bestyrelsen.'

export function SignupPage() {
  const { member, loading } = useAuth()
  const [values, setValues] = useState<SignupPayload>(EMPTY)
  // Client-side only: the server does not ask for it. Worth having anyway,
  // since there is no password reset yet — a typo here means the board
  // approves an account its owner cannot get into.
  const [repeated, setRepeated] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [receipt, setReceipt] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (loading) return <p className="status">Indlæser…</p>
  if (member) return <Navigate to="/" replace />

  function set<K extends keyof SignupPayload>(field: K, value: SignupPayload[K]) {
    setValues((current) => ({ ...current, [field]: value }))
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (values.password !== repeated) {
      setErrors({ repeated: 'De to adgangskoder er ikke ens.' })
      return
    }
    setErrors({})
    setSubmitting(true)
    try {
      const { detail } = await auth.signup(values)
      setReceipt(detail)
    } catch (caught) {
      const reported = fieldErrors(caught)
      if (Object.keys(reported).length > 0) {
        setErrors(reported)
      } else if (caught instanceof ApiError && caught.status === 429) {
        setErrors({ detail: THROTTLED })
      } else {
        setErrors({ detail: 'Anmodningen kunne ikke sendes. Prøv igen.' })
      }
    } finally {
      setSubmitting(false)
    }
  }

  if (receipt !== null) {
    return (
      <div className="login">
        <div className="card login__form">
          <h1>Tak</h1>
          <p>{receipt}</p>
          <Link to="/login">Tilbage til login</Link>
        </div>
      </div>
    )
  }

  return (
    <div className="login">
      <form className="card login__form signup" onSubmit={handleSubmit}>
        <h1>Opret bruger</h1>
        <p className="hint">
          Siden er for beboere. Står du i foreningens beboerregister, er din bruger klar med
          det samme; ellers godkender bestyrelsen den i hånden, og så går der lidt tid. Er du
          ansat eller administrator, skal du have en bruger af bestyrelsen i stedet.
        </p>

        <div className="signup__row">
          <label htmlFor="first_name">
            Fornavn
            <input
              id="first_name"
              type="text"
              autoComplete="given-name"
              required
              maxLength={150}
              value={values.first_name}
              onChange={(changed) => set('first_name', changed.target.value)}
            />
          </label>
          <label htmlFor="last_name">
            Efternavn
            <input
              id="last_name"
              type="text"
              autoComplete="family-name"
              required
              maxLength={150}
              value={values.last_name}
              onChange={(changed) => set('last_name', changed.target.value)}
            />
          </label>
        </div>
        {errors.first_name && <p className="error">{errors.first_name}</p>}
        {errors.last_name && <p className="error">{errors.last_name}</p>}

        <label htmlFor="email">
          Email
          <input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={values.email}
            onChange={(changed) => set('email', changed.target.value)}
          />
        </label>
        {errors.email && <p className="error">{errors.email}</p>}

        <label htmlFor="phone">
          Telefon <span className="signup__optional">(valgfrit)</span>
          <input
            id="phone"
            type="tel"
            autoComplete="tel"
            maxLength={32}
            value={values.phone}
            onChange={(changed) => set('phone', changed.target.value)}
          />
        </label>
        {errors.phone && <p className="error">{errors.phone}</p>}

        <label htmlFor="address">
          Adresse
          <input
            id="address"
            type="text"
            autoComplete="street-address"
            required
            maxLength={255}
            placeholder="Sankt Knuds Vej 12, 3. th"
            value={values.address}
            onChange={(changed) => set('address', changed.target.value)}
          />
        </label>
        <p className="hint">Skriv den, som den står på din post — bestyrelsen slår den op.</p>
        {errors.address && <p className="error">{errors.address}</p>}

        <label htmlFor="resident_number">
          Beboernummer
          <input
            id="resident_number"
            type="text"
            required
            maxLength={32}
            placeholder="1-2345-6789-0"
            value={values.resident_number}
            onChange={(changed) => set('resident_number', changed.target.value)}
          />
        </label>
        <p className="hint">
          Står på din huslejeopkrævning. Det er nummeret, vi genkender dig på, så tjek det en
          ekstra gang — passer det ikke, skal bestyrelsen godkende dig i hånden i stedet.
        </p>
        {errors.resident_number && <p className="error">{errors.resident_number}</p>}

        <label htmlFor="password">
          Adgangskode
          <input
            id="password"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            aria-describedby="password-rules"
            value={values.password}
            onChange={(changed) => set('password', changed.target.value)}
          />
        </label>
        {/* Django rejects a password the four validators in
            AUTH_PASSWORD_VALIDATORS dislike, and says so field by field — but
            only after a submit. Saying it up front is the difference between
            one attempt and four. Keep this list in step with that setting: it
            is a restatement of the rules, not a second implementation, and the
            server stays the authority. */}
        <ul className="signup__rules" id="password-rules">
          <li>Mindst 8 tegn</li>
          <li>Må ikke kun bestå af tal</li>
          <li>Må ikke være en af de mest almindelige adgangskoder</li>
          <li>Må ikke ligne dit navn eller din email</li>
        </ul>
        {errors.password && <p className="error">{errors.password}</p>}

        <label htmlFor="repeated">
          Gentag adgangskode
          <input
            id="repeated"
            type="password"
            autoComplete="new-password"
            required
            value={repeated}
            onChange={(changed) => setRepeated(changed.target.value)}
          />
        </label>
        {errors.repeated && <p className="error">{errors.repeated}</p>}

        {/* The honeypot. Moved off-screen rather than hidden with `display:
            none` or a `type="hidden"`, because the bots worth catching skip
            those and fill in everything else. Never tabbable, never autofilled,
            never announced — a human should not be able to reach it by
            accident, and a screen reader should not read it out. */}
        <div className="honeypot" aria-hidden="true">
          <label htmlFor="website">Website</label>
          <input
            id="website"
            name="website"
            type="text"
            tabIndex={-1}
            autoComplete="off"
            value={values.website}
            onChange={(changed) => set('website', changed.target.value)}
          />
        </div>

        {errors.detail && (
          <p role="alert" className="error">
            {errors.detail}
          </p>
        )}

        <button type="submit" disabled={submitting}>
          {submitting ? 'Sender…' : 'Send anmodning'}
        </button>
        <p className="hint">
          Har du allerede en bruger? <Link to="/login">Log ind</Link>
        </p>
      </form>
    </div>
  )
}
