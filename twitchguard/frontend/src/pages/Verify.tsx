import { useEffect, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../api'

type Status = 'checking' | 'success' | 'error'

function messageFor(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function ResendForm() {
  const [email, setEmail] = useState('')
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setMessage(null)
    try {
      const resp = await api.post<{ message: string }>('/account/resend', { email })
      setMessage({ ok: true, text: resp.message || 'Письмо отправлено повторно.' })
    } catch (err) {
      setMessage({ ok: false, text: messageFor(err, 'Не удалось отправить письмо') })
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
      <button className="btn primary" type="submit" disabled={busy}>
        Отправить письмо ещё раз
      </button>
      {message && <p className={message.ok ? 'ok-text' : 'error'}>{message.text}</p>}
    </form>
  )
}

export default function Verify() {
  const [searchParams] = useSearchParams()
  const [status, setStatus] = useState<Status>('checking')
  const [errorText, setErrorText] = useState('')

  useEffect(() => {
    const token = searchParams.get('token')
    if (!token) {
      setStatus('error')
      setErrorText('В ссылке не найден код подтверждения.')
      return
    }
    let cancelled = false
    void (async () => {
      try {
        await api.post<{ verified: true }>('/account/verify', { token })
        if (!cancelled) setStatus('success')
      } catch (err) {
        if (cancelled) return
        setStatus('error')
        setErrorText(messageFor(err, 'Не удалось подтвердить почту'))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [searchParams])

  return (
    <div className="center-screen">
      <div className="auth-card">
        <h2>Подтверждение почты</h2>
        {status === 'checking' && <p className="muted">Проверяем ссылку…</p>}
        {status === 'success' && (
          <>
            <p className="ok-text">Почта подтверждена.</p>
            <Link className="btn primary" to="/">
              Войти в кабинет
            </Link>
          </>
        )}
        {status === 'error' && (
          <>
            <p className="error">{errorText}</p>
            <p className="muted small">Запросите новую ссылку для подтверждения.</p>
            <ResendForm />
          </>
        )}
      </div>
    </div>
  )
}
