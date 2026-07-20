import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { api } from './api'
import Dashboard from './pages/Dashboard'
import Flags from './pages/Flags'
import Login from './pages/Login'
import Rules from './pages/Rules'
import Settings from './pages/Settings'
import type { Me } from './types'

interface Session {
  me: Me
  refresh: () => Promise<void>
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
    window.location.href = '/login'
  }
  return (
    <header className="nav">
      <span className="brand">🛡 TwitchGuard</span>
      <nav>
        {item('/dashboard', 'Дашборд')}
        {item('/flags', 'Флаги')}
        {item('/rules', 'Правила')}
        {me.user?.role === 'owner' && item('/settings', 'Настройки')}
      </nav>
      <div className="nav-user">
        <span className="badge">{me.user?.role === 'owner' ? 'владелец' : 'модератор'}</span>
        <span>{me.user?.login}</span>
        <button className="btn ghost" onClick={logout}>
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
      setMe({ authenticated: false })
    }
  }, [])
  useEffect(() => {
    void refresh()
  }, [refresh])

  if (me === null) return <div className="splash">Загрузка…</div>
  if (!me.authenticated) {
    return (
      <Routes>
        <Route path="*" element={<Login />} />
      </Routes>
    )
  }
  return (
    <MeContext.Provider value={{ me, refresh }}>
      <Nav me={me} />
      <main className="page">
        <Routes>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/flags" element={<Flags />} />
          <Route path="/rules" element={<Rules />} />
          {me.user?.role === 'owner' && <Route path="/settings" element={<Settings />} />}
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </main>
    </MeContext.Provider>
  )
}
