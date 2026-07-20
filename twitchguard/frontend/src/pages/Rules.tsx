import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import { useMe } from '../App'
import type { Rule, RuleValidation } from '../types'

function UploadZone({ channelId, onSaved }: { channelId: number; onSaved: () => void }) {
  const [md, setMd] = useState('')
  const [preview, setPreview] = useState<RuleValidation | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)

  const readFile = (file: File) => {
    const reader = new FileReader()
    reader.onload = () => {
      setMd(String(reader.result ?? ''))
      setPreview(null)
    }
    reader.readAsText(file)
  }

  const validate = async () => {
    setError(null)
    setPreview(
      await api.post<RuleValidation>(`/channels/${channelId}/rules/validate`, {
        md_content: md,
      }),
    )
  }

  const save = async () => {
    setError(null)
    try {
      await api.post(`/channels/${channelId}/rules`, { md_content: md })
      setMd('')
      setPreview(null)
      onSaved()
    } catch (e) {
      if (e instanceof ApiError && e.details) {
        setError(e.details.map((d) => `${d.field}: ${d.message}`).join('; '))
      } else {
        setError(e instanceof Error ? e.message : 'Ошибка сохранения')
      }
    }
  }

  return (
    <section className="panel">
      <h3>Загрузить своё правило (.md с frontmatter)</h3>
      <div
        className={dragOver ? 'dropzone over' : 'dropzone'}
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          const file = e.dataTransfer.files[0]
          if (file) readFile(file)
        }}
      >
        Перетащите md-файл сюда или{' '}
        <label className="link">
          выберите файл
          <input
            type="file"
            accept=".md,text/markdown"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) readFile(file)
            }}
          />
        </label>
      </div>
      <textarea
        rows={10}
        placeholder={'---\nname: my-rule\ntitle: Моё правило\nenabled: true\nseverity: medium\nconfidence_threshold: 0.8\n---\n\n## Что считать нарушением\n…'}
        value={md}
        onChange={(e) => {
          setMd(e.target.value)
          setPreview(null)
        }}
      />
      <div className="row">
        <button className="btn" disabled={!md.trim()} onClick={() => void validate()}>
          Проверить
        </button>
        <button
          className="btn primary"
          disabled={!preview?.valid}
          onClick={() => void save()}
          title={preview?.valid ? '' : 'Сначала успешная проверка'}
        >
          Сохранить
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {preview && !preview.valid && (
        <ul className="error">
          {preview.errors?.map((e, i) => (
            <li key={i}>
              <b>{e.field}</b>: {e.message}
            </li>
          ))}
        </ul>
      )}
      {preview?.valid && preview.frontmatter && (
        <div className="preview">
          <p>
            ✅ <b>{String(preview.frontmatter.title)}</b> ({String(preview.frontmatter.name)}) —
            severity {String(preview.frontmatter.severity)}, порог{' '}
            {String(preview.frontmatter.confidence_threshold)}
          </p>
        </div>
      )}
    </section>
  )
}

function RuleCard({
  rule,
  channelId,
  isOwner,
  onChanged,
}: {
  rule: Rule
  channelId: number
  isOwner: boolean
  onChanged: () => void
}) {
  const [versions, setVersions] = useState<Rule[] | null>(null)
  const [showBody, setShowBody] = useState(false)

  const toggle = async () => {
    await api.patch(`/channels/${channelId}/rules/${rule.name}`, { enabled: !rule.enabled })
    onChanged()
  }
  const loadVersions = async () => {
    if (versions) {
      setVersions(null)
      return
    }
    setVersions(await api.get<Rule[]>(`/channels/${channelId}/rules/${rule.name}/versions`))
  }

  return (
    <article className="rule">
      <header>
        <b>{rule.title}</b>
        <code>{rule.name}</code>
        <span className="muted small">v{rule.version}</span>
        <span className={`sev sev-${rule.severity}`}>{rule.severity}</span>
        <span className="muted small">порог {rule.confidence_threshold}</span>
        {rule.languages && <span className="muted small">языки: {rule.languages.join(', ')}</span>}
        <span className="spacer" />
        {isOwner ? (
          <label className="switch">
            <input type="checkbox" checked={rule.enabled} onChange={() => void toggle()} />
            <span>{rule.enabled ? 'включено' : 'выключено'}</span>
          </label>
        ) : (
          <span className="muted small">{rule.enabled ? 'включено' : 'выключено'}</span>
        )}
      </header>
      <div className="row">
        <button className="btn ghost" onClick={() => setShowBody(!showBody)}>
          {showBody ? 'скрыть текст' : 'показать текст'}
        </button>
        {isOwner && (
          <button className="btn ghost" onClick={() => void loadVersions()}>
            {versions ? 'скрыть версии' : 'история версий'}
          </button>
        )}
      </div>
      {showBody && <pre className="md">{rule.md_content}</pre>}
      {versions && (
        <ul className="versions">
          {versions.map((v) => (
            <li key={v.version}>
              v{v.version} {v.is_current && <b>(текущая)</b>} —{' '}
              {new Date(v.created_at).toLocaleString()}
            </li>
          ))}
        </ul>
      )}
    </article>
  )
}

export default function Rules() {
  const { me } = useMe()
  const channelId = me.channel?.id ?? 0
  const isOwner = me.user?.role === 'owner'
  const [rules, setRules] = useState<Rule[]>([])

  const load = useCallback(async () => {
    setRules(await api.get<Rule[]>(`/channels/${channelId}/rules`))
  }, [channelId])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div>
      <h2>Правила модерации</h2>
      <p className="muted">
        Изменения применяются к следующим сообщениям сразу, без перезапуска сервиса.
        {!isOwner && ' У модератора доступ только на чтение.'}
      </p>
      {rules.map((rule) => (
        <RuleCard
          key={rule.name}
          rule={rule}
          channelId={channelId}
          isOwner={isOwner}
          onChanged={() => void load()}
        />
      ))}
      {isOwner && <UploadZone channelId={channelId} onSaved={() => void load()} />}
    </div>
  )
}
