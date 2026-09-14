import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { AuthProvider, useAuth } from './auth/AuthContext'
import { Layout } from './components/Layout'
import { CalendarPage } from './pages/CalendarPage'
import { LoginPage } from './pages/LoginPage'
import { ShopRentalsPage } from './pages/ShopRentalsPage'
import { SignupPage } from './pages/SignupPage'

import type { ReactElement } from 'react'

/** Sends anonymous visitors to the login page, and optionally gates on the
 *  business committee for the erhverv section. */
function RequireAuth({
  children,
  committeeOnly = false,
}: {
  children: ReactElement
  committeeOnly?: boolean
}) {
  const { member, loading } = useAuth()

  if (loading) return <p className="status">Indlæser…</p>
  if (!member) return <Navigate to="/login" replace />
  if (committeeOnly && !member.is_business_committee) {
    return <Navigate to="/" replace />
  }
  return children
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/signup" element={<SignupPage />} />
          <Route
            element={
              <RequireAuth>
                <Layout />
              </RequireAuth>
            }
          >
            <Route path="/" element={<CalendarPage />} />
            <Route
              path="/erhverv"
              element={
                <RequireAuth committeeOnly>
                  <ShopRentalsPage />
                </RequireAuth>
              }
            />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
