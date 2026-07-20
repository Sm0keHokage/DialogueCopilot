import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import { useMe } from '../App'
import type { ChannelSettings, Moderators, WorkersSettings } from '../types'

const API_VENDORS = ['anthropic', 'openai', 'deepseek'] as const
// AC-12: список CLI-инструментов не содержит DeepSeek — у вендора нет CLI-агента.
const CLI_TOOLS = ['claude', 'gemini', 'codex'] as const

function BackendForm({
  channelId,
  settings,
  onSaved,
}: {
  channelId: number
  settings: ChannelSettings
  onSaved: () => void
}) {
  const [type, setType] = useState<'api' | 'cli'>(settings.backend.type ?? 'api')
  const [vendor, setVendor] = useState(settings.backend.vendor ?? 'anthropic')
  const [cliTool, setCliTool] = useState(settings.backend.cli_tool ?? 'claude')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState(settings.backend.model ?? '')
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const save = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const body: Record<string, unknown> = { type }
      if (type === 'api') {
        body.vendor = vendor
        if (apiKey) body.api_key = apiKey
        if (model) body.model = model
      } else {
        body.cli_tool = cliTool
      }
      await api.put(`/channels/${channelId}/settings/backend`, body)
      setApiKey('')
      setMessage({ ok: true, text: 'Backend проверен и сохранён. Применится со следующего батча.' })
      onSaved()
    } catch (e) {
      // FR-47: прежний backend остаётся активным.
      const text =
        e instanceof ApiError ? `${e.message} — прежний backend остаётся активным.` : 'Ошибка'
      setMessage({ ok: false, text })
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel">
      <h3>Backend классификации</h3>
      <p className="muted small">
        Сейчас:{' '}
        {settings.backend.type
          ? settings.backend.type === 'api'
            ? `API · ${settings.backend.vendor}${settings.backend.model ? ` · ${settings.backend.model}` : ''}`
            : `CLI · ${settings.backend.cli_tool}`
          : 'не настроен'}
      </p>
      <div className="row">
        <label>
          <input type="radio" checked={type === 'api'} onChange={() => setType('api')} /> API-ключ
        </label>
        <label>
          <input type="radio" checked={type === 'cli'} onChange={() => setType('cli')} /> Локальный
          CLI
        </label>
      </div>
      {type === 'api' ? (
        <div className="grid-form">
          <label>
            Вендор
            <select value={vendor} onChange={(e) => setVendor(e.target.value)}>
              {API_VENDORS.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label>
            API-ключ {settings.backend.has_api_key && '(сохранён — оставьте пустым, чтобы не менять)'}
            <input
              type="password"
              value={apiKey}
              placeholder="••••••••"
              onChange={(e) => setApiKey(e.target.value)}
              autoComplete="off"
            />
          </label>
          <label>
            Модель (опционально)
            <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="по умолчанию" />
          </label>
        </div>
      ) : (
        <div className="grid-form">
          <label>
            Инструмент
            <select value={cliTool} onChange={(e) => setCliTool(e.target.value)}>
              {CLI_TOOLS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <p className="muted small">
            DeepSeek доступен только как API-backend — официального CLI у вендора нет.
          </p>
        </div>
      )}
      <button className="btn primary" disabled={busy} onClick={() => void save()}>
        Проверить и сохранить
      </button>
      {message && <p className={message.ok ? 'ok-text' : 'error'}>{message.text}</p>}
    </section>
  )
}

function WorkersPanel({
  channelId,
  settings,
  onSaved,
}: {
  channelId: number
  settings: ChannelSettings
  onSaved: () => void
}) {
  const [workers, setWorkers] = useState(settings.classifier_workers)
  const [active, setActive] = useState<number | null>(null)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const save = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const resp = await api.put<WorkersSettings>(`/channels/${channelId}/settings/workers`, {
        workers,
      })
      setWorkers(resp.workers)
      setActive(resp.active)
      setMessage({ ok: true, text: `Сохранено. Сейчас активно агентов: ${resp.active}.` })
      onSaved()
    } catch (e) {
      setMessage({ ok: false, text: e instanceof ApiError ? e.message : 'Не удалось сохранить' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel">
      <h3>Параллельные ИИ-агенты</h3>
      <p className="muted small">
        Для стримов с большим онлайном: несколько агентов разбирают чат параллельно. Аккаунт
        Twitch при этом один — масштабируется только классификация.
      </p>
      <div className="row">
        <input
          type="range"
          min={1}
          max={settings.max_workers}
          value={workers}
          onChange={(e) => setWorkers(Number(e.target.value))}
        />
        <input
          className="num-sm"
          type="number"
          min={1}
          max={settings.max_workers}
          value={workers}
          onChange={(e) => setWorkers(Number(e.target.value))}
        />
        <span className="muted small">из {settings.max_workers}</span>
      </div>
      <button className="btn primary" disabled={busy} onClick={() => void save()}>
        Сохранить
      </button>
      {active !== null && <p className="muted small">Активно агентов сейчас: {active}</p>}
      {message && <p className={message.ok ? 'ok-text' : 'error'}>{message.text}</p>}
    </section>
  )
}

function ActionProxy({
  channelId,
  settings,
  onSaved,
}: {
  channelId: number
  settings: ChannelSettings
  onSaved: () => void
}) {
  const [reauthUrl, setReauthUrl] = useState<string | null>(null)
  const toggle = async () => {
    const resp = await api.put<{ enabled: boolean; reauth_required: boolean; reauth_url: string | null }>(
      `/channels/${channelId}/settings/action-proxy`,
      { enabled: !settings.action_proxy_enabled },
    )
    setReauthUrl(resp.reauth_required ? resp.reauth_url : null)
    onSaved()
  }
  return (
    <section className="panel">
      <h3>Action Proxy</h3>
      <p className="muted small">
        Позволяет модератору кнопкой применить действие через Helix API от своего имени. Система
        сама никого не наказывает — advisory-режим сохраняется.
      </p>
      <label className="switch">
        <input
          type="checkbox"
          checked={settings.action_proxy_enabled}
          onChange={() => void toggle()}
        />
        <span>{settings.action_proxy_enabled ? 'включён' : 'выключен'}</span>
      </label>
      {reauthUrl && (
        <p className="banner warn">
          Нужны дополнительные права Twitch.{' '}
          <a className="link" href={reauthUrl}>
            Переподключиться с расширенными scope
          </a>
        </p>
      )}
    </section>
  )
}

function ModeratorsPanel({ channelId }: { channelId: number }) {
  const [mods, setMods] = useState<Moderators['moderators']>([])
  const [login, setLogin] = useState('')
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    const body = await api.get<Moderators>(`/channels/${channelId}/moderators`)
    setMods(body.moderators)
  }, [channelId])

  useEffect(() => {
    void load()
  }, [load])

  const add = async () => {
    setError(null)
    try {
      await api.post(`/channels/${channelId}/moderators`, { login })
      setLogin('')
      void load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка')
    }
  }
  const remove = async (name: string) => {
    await api.del(`/channels/${channelId}/moderators/${name}`)
    void load()
  }

  return (
    <section className="panel">
      <h3>Модераторы</h3>
      <div className="row">
        <input
          placeholder="twitch-логин"
          value={login}
          onChange={(e) => setLogin(e.target.value)}
        />
        <button className="btn" disabled={!login} onClick={() => void add()}>
          Пригласить
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      <ul className="mods">
        {mods.map((m) => (
          <li key={m.login}>
            {m.login} {m.registered ? '· зарегистрирован' : '· ожидает первого входа'}
            <button className="btn ghost" onClick={() => void remove(m.login)}>
              убрать
            </button>
          </li>
        ))}
        {mods.length === 0 && <li className="muted">Пока никого.</li>}
      </ul>
    </section>
  )
}

export default function Settings() {
  const { me, refresh } = useMe()
  const channelId = me.channel?.id ?? 0
  const [settings, setSettings] = useState<ChannelSettings | null>(null)

  const load = useCallback(async () => {
    setSettings(await api.get<ChannelSettings>(`/channels/${channelId}/settings`))
    void refresh()
  }, [channelId, refresh])

  useEffect(() => {
    void load()
  }, [load])

  const disconnect = async () => {
    if (!window.confirm('Отключить канал? Токены будут отозваны, слушатель остановлен.')) return
    await api.post(`/channels/${channelId}/disconnect`)
    window.location.href = '/'
  }

  if (!settings) return <p className="muted">Загрузка…</p>
  return (
    <div>
      <h2>Настройки</h2>
      <BackendForm channelId={channelId} settings={settings} onSaved={() => void load()} />
      <WorkersPanel channelId={channelId} settings={settings} onSaved={() => void load()} />
      <ActionProxy channelId={channelId} settings={settings} onSaved={() => void load()} />
      <ModeratorsPanel channelId={channelId} />
      <section className="panel danger-zone">
        <h3>Отключение канала</h3>
        <p className="muted small">
          Отзывает токены на стороне Twitch, удаляет их из базы и останавливает EventSub (FR-09).
        </p>
        <button className="btn danger" onClick={() => void disconnect()}>
          Отключить канал
        </button>
      </section>
    </div>
  )
}
