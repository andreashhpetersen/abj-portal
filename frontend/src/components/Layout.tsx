import { NavLink, Outlet } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'

/** App shell: header, navigation, and the routed page below it. */
export function Layout() {
  const { member, logout } = useAuth()

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header__brand">Beboerportal</div>
        <nav className="app-nav">
          <NavLink to="/" end>
            Kalender
          </NavLink>
          {member?.is_business_committee && <NavLink to="/erhverv">Erhvervslejemål</NavLink>}
        </nav>
        <div className="app-header__user">
          <span>{member?.email}</span>
          <button type="button" onClick={() => void logout()}>
            Log ud
          </button>
        </div>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  )
}
