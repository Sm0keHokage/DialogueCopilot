import { useState, type FormEvent } from 'react'
import { api, ApiError } from '../api'

type Tab = 'login' | 'register'

interface LandingProps {
  onAuthenticated: () => Promise<void>
}

interface PulseLine {
  author: string
  text: string
  flag?: boolean
}

const CHAT_LINES: PulseLine[] = [
  { author: 'viewer_92', text: 'го ивент сегодня?' },
  { author: 'nika_tv', text: 'красивая карта, го дальше' },
  { author: 'troll_x', text: 'бесплатные подписки тут — переходи по ссылке', flag: true },
  { author: 'mods_here', text: 'не забудьте зафоловить канал' },
  { author: 'gg_wp', text: 'LUL LUL слишком сложно' },
  { author: 'ember', text: 'го дуо после стрима' },
]

function ChatPulse() {
  const lines = [...CHAT_LINES, ...CHAT_LINES]
  return (
    <div className="chat-pulse" aria-hidden="true">
      <div className="chat-pulse-track">
        {lines.map((line, i) => (
          <p key={i} className={line.flag ? 'cp-line cp-flag' : 'cp-line'}>
            <span className="cp-author">{line.author}:</span> <span className="cp-text">{line.text}</span>
            {line.flag && <span className="cp-chip">⚑ spam · 0.93</span>}
          </p>
        ))}
      </div>
    </div>
  )
}

function messageFor(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function LoginForm({ onAuthenticated }: { onAuthenticated: () => Promise<void> }) {
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await api.post<{ ok: true }>('/account/login', { login, password })
      await onAuthenticated()
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError('Слишком много попыток, подождите 15 минут')
      } else {
        setError(messageFor(err, 'Не удалось войти'))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="grid-form" onSubmit={(e) => void submit(e)}>
      <label>
        Почта или ник
        <input
          value={login}
          onChange={(e) => setLogin(e.target.value)}
          autoComplete="username"
          required
        />
      </label>
      <label>
        Пароль
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button className="btn primary" type="submit" disabled={busy}>
        Войти
      </button>
    </form>
  )
}

function RegisterForm() {
  const [email, setEmail] = useState('')
  const [nick, setNick] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)

  if (done) {
    return (
      <p className="notice">Проверьте почту — мы отправили ссылку для подтверждения на {email}.</p>
    )
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (password.length < 8) {
      setError('Пароль должен быть не короче 8 символов')
      return
    }
    setBusy(true)
    try {
      await api.post<{ message: string }>('/account/register', { email, nick, password })
      setDone(true)
    } catch (err) {
      setError(messageFor(err, 'Не удалось зарегистрироваться'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="grid-form" onSubmit={(e) => void submit(e)}>
      <label>
        Почта
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          required
        />
      </label>
      <label>
        Ник
        <input value={nick} onChange={(e) => setNick(e.target.value)} autoComplete="nickname" required />
      </label>
      <label>
        Пароль
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
          minLength={8}
          required
        />
        <span className="muted small">не короче 8 символов</span>
      </label>
      {error && <p className="error">{error}</p>}
      <button className="btn primary" type="submit" disabled={busy}>
        Зарегистрироваться
      </button>
    </form>
  )
}

export default function Landing({ onAuthenticated }: LandingProps) {
  const [tab, setTab] = useState<Tab>('login')

  return (
    <div className="landing">
      <div className="landing-left">
        <h1>TwitchGuard</h1>
        <p className="landing-thesis">ИИ читает чат — решение принимает человек.</p>
        <ChatPulse />
        <ul className="landing-facts">
          <li>
            <span className="fact-dot" />
            Официальный Twitch EventSub — никаких парсеров и ботов в чате
          </li>
          <li>
            <span className="fact-dot" />
            Параллельные ИИ-агенты для чатов с большим онлайном
          </li>
          <li>
            <span className="fact-dot" />
            Каждый флаг разбирает живой модератор
          </li>
        </ul>
      </div>
      <div className="landing-right">
        <div className="auth-card">
          <div className="auth-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'login'}
              className={tab === 'login' ? 'active' : undefined}
              onClick={() => setTab('login')}
            >
              Вход
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'register'}
              className={tab === 'register' ? 'active' : undefined}
              onClick={() => setTab('register')}
            >
              Регистрация
            </button>
          </div>
          {tab === 'login' ? <LoginForm onAuthenticated={onAuthenticated} /> : <RegisterForm />}
        </div>
        <div className="auth-alt">
          <div className="auth-divider">
            <span>или</span>
          </div>
          <a className="btn" href="/auth/twitch/login">
            Войти через Twitch
          </a>
          <p className="muted small">Пароль от Twitch мы никогда не запрашиваем.</p>
        </div>
      </div>
    </div>
  )
}
