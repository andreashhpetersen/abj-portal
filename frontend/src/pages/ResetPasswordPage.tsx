/**
 * Where the reset email's link lands — `uid` and `token` are read straight
 * out of the route rather than typed, so this page never sees the current
 * session and works exactly the same logged in or logged out.
 */

import { useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'

import { auth } from '../api/auth'
import { fieldErrors } from '../api/client'
import logo from '../assets/logo.png'

import type { FormEvent } from 'react'

export function ResetPasswordPage() {
  const { uid, token } = useParams<{ uid: string; token: string }>()
  const [newPassword, setNewPassword] = useState('')
  const [repeated, setRepeated] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [done, setDone] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  if (!uid || !token) return <Navigate to="/forgot-password" replace />

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (newPassword !== repeated) {
      setErrors({ repeated: 'De to adgangskoder er ikke ens.' })
      return
    }
    setErrors({})
    setSubmitting(true)
    try {
      await auth.confirmPasswordReset(uid as string, token as string, newPassword)
      setDone(true)
    } catch (caught) {
      const reported = fieldErrors(caught)
      setErrors(
        Object.keys(reported).length > 0
          ? reported
          : { detail: 'Adgangskoden kunne ikke nulstilles. Prøv igen.' },
      )
    } finally {
      setSubmitting(false)
    }
  }

  if (done) {
    return (
      <div className="login">
        <div className="login__brand">
          <img src={logo} alt="A/B Jæger" className="login__logo" />
        </div>
        <div className="card login__form">
          <h1>Adgangskoden er nulstillet</h1>
          <p>Du kan nu logge ind med din nye adgangskode.</p>
          <Link to="/login">Til login</Link>
        </div>
      </div>
    )
  }

  return (
    <div className="login">
      <div className="login__brand">
        <img src={logo} alt="A/B Jæger" className="login__logo" />
      </div>
      <form className="card login__form signup" onSubmit={handleSubmit}>
        <h1>Vælg ny adgangskode</h1>

        <label htmlFor="new_password">
          Ny adgangskode
          <input
            id="new_password"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            aria-describedby="password-rules"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
          />
        </label>
        {/* Restates AUTH_PASSWORD_VALIDATORS, same as SignupPage — keep the two
            lists in step with that setting. */}
        <ul className="signup__rules" id="password-rules">
          <li>Mindst 8 tegn</li>
          <li>Må ikke kun bestå af tal</li>
          <li>Må ikke være en af de mest almindelige adgangskoder</li>
          <li>Må ikke ligne dit navn eller din email</li>
        </ul>
        {errors.new_password && <p className="error">{errors.new_password}</p>}

        <label htmlFor="repeated">
          Gentag adgangskode
          <input
            id="repeated"
            type="password"
            autoComplete="new-password"
            required
            value={repeated}
            onChange={(event) => setRepeated(event.target.value)}
          />
        </label>
        {errors.repeated && <p className="error">{errors.repeated}</p>}

        {errors.detail && (
          <p role="alert" className="error">
            {errors.detail}
          </p>
        )}

        <button type="submit" disabled={submitting}>
          {submitting ? 'Gemmer…' : 'Gem ny adgangskode'}
        </button>
        <p className="hint">
          Linket ikke gyldigt længere? <Link to="/forgot-password">Bed om et nyt</Link>
        </p>
      </form>
    </div>
  )
}
