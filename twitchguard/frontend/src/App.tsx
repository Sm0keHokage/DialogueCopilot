import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { api } from './api'
import Account from './pages/Account'
import Dashboard from './pages/Dashboard'
import Flags from './pages/Flags'
import Landing from './pages/Landing'
import Rules from './pages/Rules'
import Settings from './pages/Settings'
import Verify from './pages/Verify'
import type { Me } from './types'

interface Session {
  me: Me
  refresh: () => Promise<void>
}

const EMPTY_ME: Me = {
  authenticated: false,
  account: null,
  twitch_linked: false,
  user: null,
  channel: null,
  can_action: false,
}

const MeContext = createContext<Session | null>(null)

export function useMe(): Session {
  const ctx = useContext(MeContext)
  if (!ctx) throw new Error('useMe outside provider')
  return ctx
}

function Nav({ me }: { me: Me }) {
  const location = useLocation()
  const item = (to: string, label: string) => (
    <Link className={location.pathname === to ? 'nav-link active' : 'nav-link'} to={to}>
      {label}
    </Link>
  )
  const logout = async () => {
    await api.post('/auth/logout')
    window.location.href = '/'
  }
  return (
    <header className="nav">
      <span className="brand">TwitchGuard</span>
      <nav>
        {me.twitch_linked && item('/dashboard', 'Дашборд')}
        {me.twitch_linked && item('/flags', 'Флаги')}
        {me.twitch_linked && item('/rules', 'Правила')}
        {me.twitch_linked && me.user?.role === 'owner' && item('/settings', 'Настройки')}
        {item('/account', 'Кабинет')}
      </nav>
      <div className="nav-user">
        {me.user && (
          <span className="badge">{me.user.role === 'owner' ? 'владелец' : 'модератор'}</span>
        )}
        <span>{me.account?.nick ?? me.user?.login}</span>
        <button className="btn ghost" onClick={() => void logout()}>
          Выйти
        </button>
      </div>
    </header>
  )
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null)
  const refresh = useCallback(async () => {
    try {
      setMe(await api.get<Me>('/auth/me'))
    } catch {
      setMe(EMPTY_ME)
    }
  }, [])
  useEffect(() => {
    void refresh()
  }, [refresh])

  if (me === null) return <div className="splash">Загрузка…</div>

  // State (a): not authenticated — only the landing page and the (unauthenticated)
  // email-verification page are reachable.
  if (!me.authenticated) {
    return (
      <Routes>
        <Route path="/verify" element={<Verify />} />
        <Route path="*" element={<Landing onAuthenticated={refresh} />} />
      </Routes>
    )
  }

  // State (b): account exists but no Twitch channel is linked yet — the cabinet
  // (with its "Подключить Twitch" CTA) is the only reachable place.
  if (!me.twitch_linked) {
    return (
      <MeContext.Provider value={{ me, refresh }}>
        <Nav me={me} />
        <main className="page">
          <Routes>
            <Route path="/account" element={<Account />} />
            <Route path="*" element={<Navigate to="/account" replace />} />
          </Routes>
        </main>
      </MeContext.Provider>
    )
  }

  // State (c): fully onboarded — the regular moderation console plus the cabinet.
  return (
    <MeContext.Provider value={{ me, refresh }}>
      <Nav me={me} />
      <main className="page">
        <Routes>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/flags" element={<Flags />} />
          <Route path="/rules" element={<Rules />} />
          {me.user?.role === 'owner' && <Route path="/settings" element={<Settings />} />}
          <Route path="/account" element={<Account />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </main>
    </MeContext.Provider>
  )
}
