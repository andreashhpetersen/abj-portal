import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'

import type { FormEvent } from 'react'

export function LoginPage() {
  const { member, loading, login } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (loading) return <p className="status">Indlæser…</p>
  if (member) return <Navigate to="/" replace />

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email, password)
      navigate('/', { replace: true })
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : 'Noget gik galt. Prøv igen.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="login">
      <form className="card login__form" onSubmit={handleSubmit}>
        <h1>Log ind</h1>
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <label htmlFor="password">Adgangskode</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {error && <p role="alert" className="error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Logger ind…' : 'Log ind'}
        </button>
      </form>
    </div>
  )
}
