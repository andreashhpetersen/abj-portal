import { NavLink, Outlet } from 'react-router-dom'

import logo from '../assets/logo.png'
import { useAuth } from '../auth/AuthContext'

/** App shell: header, navigation, and the routed page below it. */
export function Layout() {
  const { member, logout } = useAuth()

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header__brand">
          <img src={logo} alt="" className="app-header__logo" />
          Beboerportal
        </div>
        <nav className="app-nav">
          <NavLink to="/" end>
            Kalender
          </NavLink>
          {member?.is_business_committee && <NavLink to="/erhverv">Erhvervslejemål</NavLink>}
          {/* A plain anchor, not a NavLink: the Django admin is a separate app
              served by the backend, so this must be a full page load. */}
          {member?.is_staff && <a href="/admin/">Administration</a>}
        </nav>
        <div className="app-header__user">
          {/* Non-residents have no address, so fall back to the email alone. */}
          <span>
            {member?.resident
              ? `${member.email} · ${member.resident.address}`
              : member?.email}
          </span>
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
