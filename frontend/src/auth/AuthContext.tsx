/**
 * Holds the logged-in member for the whole app.
 *
 * On mount it asks the API who the current session belongs to, so a page
 * refresh keeps you logged in without storing anything in localStorage.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { ApiError, api } from '../api/client'

export interface Building {
  id: number
  street: string
  house_number: string
  name: string
  /** Street and number on one line, e.g. "Sankt Knuds Vej 12". */
  label: string
}

/** Residency details. Owned by the association's other database. */
export interface Resident {
  external_user_id: number
  /** Format: 1-2345-6789-0. */
  resident_number: string
  building: Building
  floor: string
  door: string
  /** Full address on one line, e.g. "Sankt Knuds Vej 12, 3. th". */
  address: string
}

export interface Member {
  id: number
  email: string
  first_name: string
  last_name: string
  phone: string
  is_staff: boolean
  is_business_committee: boolean
  /** Null for users who are not residents — employees, third-party managers. */
  resident: Resident | null
}

interface AuthContextValue {
  member: Member | null
  /** True until the initial "who am I" request settles. */
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [member, setMember] = useState<Member | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    async function restoreSession() {
      try {
        await api.ensureCsrf()
        const current = await api.get<Member>('/auth/me/')
        if (!cancelled) setMember(current)
      } catch (error) {
        // 401/403 simply means "not logged in" — anything else is worth seeing.
        if (!(error instanceof ApiError) || error.status >= 500) {
          console.error('Kunne ikke hente session', error)
        }
        if (!cancelled) setMember(null)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void restoreSession()
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    await api.ensureCsrf()
    setMember(await api.post<Member>('/auth/login/', { email, password }))
  }, [])

  const logout = useCallback(async () => {
    await api.post('/auth/logout/')
    setMember(null)
  }, [])

  const value = useMemo(
    () => ({ member, loading, login, logout }),
    [member, loading, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth skal bruges inde i en <AuthProvider>')
  }
  return context
}
