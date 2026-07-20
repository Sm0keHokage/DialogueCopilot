import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../api'
import { useMe } from '../App'
import type { Me } from '../types'

const PLAN_FEATURES = [
  'Параллельные ИИ-агенты',
  'Свои правила модерации',
  'Живая очередь флагов',
  'Action Proxy',
  'Precision-метрики',
]

function messageFor(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function ProfileCard({ email, nick, verified }: { email: string; nick: string; verified: boolean }) {
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const resend = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const resp = await api.post<{ message: string }>('/account/resend', { email })
      setMessage({ ok: true, text: resp.message || 'Письмо отправлено.' })
    } catch (err) {
      setMessage({ ok: false, text: messageFor(err, 'Не удалось отправить письмо') })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <h3>Профиль</h3>
      <dl className="kv">
        <div>
          <dt>Ник</dt>
          <dd>{nick}</dd>
        </div>
        <div>
          <dt>Почта</dt>
          <dd>{email}</dd>
        </div>
      </dl>
      {verified ? (
        <span className="status ok">почта подтверждена</span>
      ) : (
        <>
          <p className="banner warn">Почта не подтверждена — часть функций может быть ограничена.</p>
          <button className="btn" disabled={busy} onClick={() => void resend()}>
            Отправить письмо ещё раз
          </button>
        </>
      )}
      {message && <p className={message.ok ? 'ok-text small' : 'error small'}>{message.text}</p>}
    </div>
  )
}

function TwitchCard({ me }: { me: Me }) {
  const linked = me.twitch_linked && me.channel !== null
  const verified = me.account?.email_verified ?? false
  const statusClass =
    me.channel?.eventsub_status === 'active' ? 'ok' : me.channel?.needs_reauth ? 'bad' : 'warn'

  return (
    <div className={linked ? 'card' : 'card highlight'}>
      <h3>Twitch</h3>
      {linked && me.channel ? (
        <>
          <p>{me.channel.display_name ?? me.user?.login}</p>
          <p>
            <span className={`status ${statusClass}`}>{me.channel.eventsub_status}</span>
          </p>
          <Link className="link" to="/dashboard">
            Перейти в дашборд →
          </Link>
        </>
      ) : (
        <>
          <p className="muted small">Подключите канал Twitch, чтобы включить модерацию чата.</p>
          {verified ? (
            <a className="btn primary lg" href="/auth/twitch/login">
              Подключить Twitch
            </a>
          ) : (
            <>
              <button className="btn primary lg" disabled title="Сначала подтвердите почту">
                Подключить Twitch
              </button>
              <p className="muted small">Сначала подтвердите почту</p>
            </>
          )}
        </>
      )}
    </div>
  )
}

function PlanCard() {
  return (
    <div className="card">
      <h3>Тариф</h3>
      <p className="plan-name">Бесплатно · бета</p>
      <ul className="check-list">
        {PLAN_FEATURES.map((f) => (
          <li key={f}>{f}</li>
        ))}
      </ul>
      <p className="muted small">Пока идёт бета — всё бесплатно.</p>
    </div>
  )
}

function SecurityPanel() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [logoutBusy, setLogoutBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setMessage(null)
    if (next.length < 8) {
      setMessage({ ok: false, text: 'Новый пароль должен быть не короче 8 символов' })
      return
    }
    setBusy(true)
    try {
      await api.post<void>('/account/password', { current_password: current, new_password: next })
      setCurrent('')
      setNext('')
      setMessage({ ok: true, text: 'Пароль обновлён.' })
    } catch (err) {
      setMessage({ ok: false, text: messageFor(err, 'Не удалось сохранить пароль') })
    } finally {
      setBusy(false)
    }
  }

  const logoutAll = async () => {
    setLogoutBusy(true)
    try {
      await api.post<void>('/account/logout-all')
      window.location.href = '/'
    } catch (err) {
      setMessage({ ok: false, text: messageFor(err, 'Не удалось выйти на всех устройствах') })
      setLogoutBusy(false)
    }
  }

  return (
    <section className="panel">
      <h3>Безопасность</h3>
      <form className="grid-form" onSubmit={(e) => void submit(e)}>
        <label>
          Текущий пароль
          <input
            type="password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        <label>
          Новый пароль
          <input
            type="password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            autoComplete="new-password"
            minLength={8}
            required
          />
        </label>
        <div className="row">
          <button className="btn primary" type="submit" disabled={busy}>
            Сохранить
          </button>
        </div>
      </form>
      {message && <p className={message.ok ? 'ok-text' : 'error'}>{message.text}</p>}
      <hr className="divider" />
      <div className="row">
        <p className="muted small spacer">
          «Выйти на всех устройствах» завершит все активные сеансы, включая этот — потребуется
          войти заново.
        </p>
        <button className="btn danger" disabled={logoutBusy} onClick={() => void logoutAll()}>
          Выйти на всех устройствах
        </button>
      </div>
    </section>
  )
}

export default function Account() {
  const { me } = useMe()
  if (!me.account) return <p className="muted">Загрузка…</p>
  const { email, nick, email_verified: verified } = me.account

  return (
    <div>
      <h2>Личный кабинет</h2>
      <div className="cards">
        {me.twitch_linked ? (
          <>
            <ProfileCard email={email} nick={nick} verified={verified} />
            <TwitchCard me={me} />
            <PlanCard />
          </>
        ) : (
          <>
            <TwitchCard me={me} />
            <ProfileCard email={email} nick={nick} verified={verified} />
            <PlanCard />
          </>
        )}
      </div>
      <SecurityPanel />
    </div>
  )
}
