import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import { useMe } from '../App'
import type { Flag, StreamEvent } from '../types'
import { useChannelStream } from '../ws'

const SEVERITY_LABEL: Record<string, string> = { low: 'низкая', medium: 'средняя', high: 'высокая' }

function FlagCard({
  flag,
  canAction,
  onUpdate,
  onError,
}: {
  flag: Flag
  canAction: boolean
  onUpdate: (updated: Flag) => void
  onError: (message: string) => void
}) {
  const { me } = useMe()
  const cid = me.channel?.id
  const [actionType, setActionType] = useState<'delete' | 'timeout' | 'ban'>('delete')
  const [duration, setDuration] = useState(600)
  const [busy, setBusy] = useState(false)

  const setStatus = async (status: 'reviewed' | 'dismissed') => {
    setBusy(true)
    try {
      onUpdate(await api.patch<Flag>(`/channels/${cid}/flags/${flag.id}`, { status }))
    } catch (e) {
      onError(e instanceof ApiError ? e.message : 'Не удалось изменить статус')
    } finally {
      setBusy(false)
    }
  }

  const applyAction = async () => {
    setBusy(true)
    try {
      const body: { type: string; duration_s?: number } = { type: actionType }
      if (actionType === 'timeout') body.duration_s = duration
      onUpdate(await api.post<Flag>(`/channels/${cid}/flags/${flag.id}/action`, body))
    } catch (e) {
      onError(e instanceof ApiError ? e.message : 'Действие не применено')
    } finally {
      setBusy(false)
    }
  }

  const terminal = flag.status === 'dismissed' || flag.status === 'actioned'
  return (
    <article className={`flag sev-${flag.severity} st-${flag.status}`}>
      <header>
        <span className={`sev sev-${flag.severity}`}>{SEVERITY_LABEL[flag.severity]}</span>
        <b>{flag.rule_name}</b>
        <span className="muted small">v{flag.rule_version}</span>
        <span className="muted small">
          уверенность {Math.round(flag.confidence * 100)}%
        </span>
        <span className={`status st-${flag.status}`}>{flag.status}</span>
      </header>
      <p className="msg">
        <b>{flag.author_login}:</b> {flag.message_text}
      </p>
      <p className="muted">💬 {flag.reason}</p>
      {flag.action_hint && <p className="muted small">подсказка ИИ: {flag.action_hint}</p>}
      {!terminal && (
        <footer>
          <button className="btn" disabled={busy} onClick={() => void setStatus('reviewed')}>
            Просмотрено
          </button>
          <button className="btn ghost" disabled={busy} onClick={() => void setStatus('dismissed')}>
            Отклонить (ложное)
          </button>
          {canAction && (
            <span className="action-group">
              <select
                value={actionType}
                onChange={(e) => setActionType(e.target.value as 'delete' | 'timeout' | 'ban')}
              >
                <option value="delete">удалить сообщение</option>
                <option value="timeout">таймаут</option>
                <option value="ban">бан</option>
              </select>
              {actionType === 'timeout' && (
                <input
                  type="number"
                  min={1}
                  value={duration}
                  onChange={(e) => setDuration(Number(e.target.value))}
                  title="секунд"
                />
              )}
              <button className="btn danger" disabled={busy} onClick={() => void applyAction()}>
                Применить действие
              </button>
            </span>
          )}
        </footer>
      )}
    </article>
  )
}

export default function Flags() {
  const { me } = useMe()
  const channelId = me.channel?.id ?? null
  const [flags, setFlags] = useState<Flag[]>([])
  const [filters, setFilters] = useState({ status: 'new', rule: '', severity: '', author: '' })
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!channelId) return
    const params = new URLSearchParams()
    for (const [k, v] of Object.entries(filters)) if (v) params.set(k, v)
    params.set('limit', '100')
    const body = await api.get<{ items: Flag[] }>(`/channels/${channelId}/flags?${params}`)
    setFlags(body.items)
  }, [channelId, filters])

  useEffect(() => {
    void load()
  }, [load])

  useChannelStream(channelId, (event: StreamEvent) => {
    if (event.type === 'flag.created') {
      const flag = event.data as Flag
      setFlags((prev) => (prev.some((f) => f.id === flag.id) ? prev : [flag, ...prev]))
    } else if (event.type === 'flag.updated') {
      const flag = event.data as Flag
      setFlags((prev) => prev.map((f) => (f.id === flag.id ? flag : f)))
    }
  })

  const patch = (updated: Flag) =>
    setFlags((prev) => prev.map((f) => (f.id === updated.id ? updated : f)))

  const shown = flags.filter((f) => !filters.status || f.status === filters.status)

  return (
    <div>
      <h2>Очередь флагов</h2>
      {error && (
        <p className="banner bad" onClick={() => setError(null)}>
          {error} ✕
        </p>
      )}
      <div className="filters">
        <select
          value={filters.status}
          onChange={(e) => setFilters({ ...filters, status: e.target.value })}
        >
          <option value="">все статусы</option>
          <option value="new">new</option>
          <option value="reviewed">reviewed</option>
          <option value="dismissed">dismissed</option>
          <option value="actioned">actioned</option>
        </select>
        <select
          value={filters.severity}
          onChange={(e) => setFilters({ ...filters, severity: e.target.value })}
        >
          <option value="">любая severity</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
        </select>
        <input
          placeholder="правило"
          value={filters.rule}
          onChange={(e) => setFilters({ ...filters, rule: e.target.value })}
        />
        <input
          placeholder="автор"
          value={filters.author}
          onChange={(e) => setFilters({ ...filters, author: e.target.value })}
        />
      </div>
      {shown.length === 0 && <p className="muted">Флагов нет — чат чист 🎉</p>}
      {shown.map((flag) => (
        <FlagCard
          key={flag.id}
          flag={flag}
          canAction={Boolean(me.can_action)}
          onUpdate={patch}
          onError={setError}
        />
      ))}
    </div>
  )
}
