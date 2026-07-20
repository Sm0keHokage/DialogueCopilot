import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useMe } from '../App'
import type { Dashboard as DashboardData, StreamEvent } from '../types'
import { useChannelStream } from '../ws'

interface ChatLine {
  author_login: string
  text: string
  ts_ms: number
}

export default function Dashboard() {
  const { me } = useMe()
  const channelId = me.channel?.id ?? null
  const [data, setData] = useState<DashboardData | null>(null)
  const [chat, setChat] = useState<ChatLine[]>([])
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!channelId) return
    try {
      const d = await api.get<DashboardData>(`/channels/${channelId}/dashboard`)
      setData(d)
      setChat(
        d.recent_messages
          .map((m) => ({ author_login: m.author_login, text: m.text, ts_ms: Number(m.ts_ms) }))
          .reverse(),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка загрузки')
    }
  }, [channelId])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 10000)
    return () => window.clearInterval(timer)
  }, [load])

  useChannelStream(channelId, (event: StreamEvent) => {
    if (event.type === 'chat.message') {
      const line = event.data as ChatLine
      setChat((prev) => [...prev.slice(-49), line])
    }
  })

  const restartEventSub = async () => {
    if (!channelId) return
    await api.post(`/channels/${channelId}/eventsub/restart`)
    void load()
  }

  if (error) return <p className="error">{error}</p>
  if (!data) return <p className="muted">Загрузка…</p>

  const statusClass =
    data.channel.eventsub_status === 'active' ? 'ok' : data.channel.needs_reauth ? 'bad' : 'warn'

  return (
    <div>
      <h2>Дашборд — {data.channel.display_name}</h2>
      {data.channel.needs_reauth && (
        <p className="banner bad">
          Требуется переподключение канала.{' '}
          <a className="link" href="/auth/twitch/login">
            Переподключить через Twitch
          </a>
        </p>
      )}
      <div className="cards">
        <div className="card">
          <h3>EventSub</h3>
          <p>
            <span className={`status ${statusClass}`}>{data.channel.eventsub_status}</span>
          </p>
          <button className="btn" onClick={restartEventSub}>
            Повторить подписку
          </button>
        </div>
        <div className="card">
          <h3>Backend модели</h3>
          {data.backend.configured ? (
            <p>
              {data.backend.type === 'api'
                ? `API: ${data.backend.vendor}`
                : `CLI: ${data.backend.cli_tool}`}
            </p>
          ) : (
            <p className="warn-text">не настроен — классификация не идёт</p>
          )}
          <p className="muted small">
            ИИ-агентов: {data.workers.configured} (активно {data.workers.active})
          </p>
        </div>
        <div className="card">
          <h3>Сегодня</h3>
          <p>
            сообщений: <b>{data.today.messages_processed}</b> · флагов:{' '}
            <b>{data.today.flags_created}</b> · сбоев: <b>{data.today.classification_failed}</b>
          </p>
          <p className="muted small">
            всего: {data.total.messages_processed} сообщений, {data.total.tokens} токенов
            {data.total.cost_usd > 0 && <> (~${data.total.cost_usd})</>}
          </p>
        </div>
        <div className="card">
          <h3>Задержка классификации</h3>
          <p>
            p50: <b>{data.latency_ms.p50 ? `${Math.round(data.latency_ms.p50)} мс` : '—'}</b> ·
            p95: <b>{data.latency_ms.p95 ? `${Math.round(data.latency_ms.p95)} мс` : '—'}</b>
          </p>
          <p className="muted small">отставание очереди: {data.backlog} сообщ.</p>
        </div>
      </div>

      {data.channel.display_name && (
        <section className="panel">
          <h3>Трансляция</h3>
          <div className="player-16x9">
            <iframe
              src={`https://player.twitch.tv/?channel=${data.channel.display_name}&parent=${window.location.hostname}&muted=true`}
              allowFullScreen
              title="Трансляция Twitch"
            />
          </div>
          <p className="muted small">
            Официальный плеер Twitch — звук и видео идут напрямую с Twitch, TwitchGuard медиа не
            обрабатывает.
          </p>
        </section>
      )}

      <div className="columns">
        <section className="panel">
          <h3>Precision по правилам (FR-36)</h3>
          {data.precision.length === 0 ? (
            <p className="muted">Пока нет разобранных флагов.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Правило</th>
                  <th>Флагов</th>
                  <th>Отклонено</th>
                  <th>Подтверждено</th>
                  <th>Precision</th>
                </tr>
              </thead>
              <tbody>
                {data.precision.map((p) => (
                  <tr key={p.rule_name}>
                    <td>{p.rule_name}</td>
                    <td>{p.flags_total}</td>
                    <td>{p.dismissed}</td>
                    <td>{p.confirmed}</td>
                    <td>{p.precision === null ? '—' : `${Math.round(p.precision * 100)}%`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        <section className="panel">
          <h3>Живой чат</h3>
          <div className="chatlog">
            {chat.length === 0 && <p className="muted">Сообщений пока нет.</p>}
            {chat.map((line, i) => (
              <p key={`${line.ts_ms}-${i}`}>
                <b>{line.author_login}:</b> {line.text}
              </p>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
