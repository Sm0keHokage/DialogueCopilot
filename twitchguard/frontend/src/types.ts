export interface Me {
  authenticated: boolean
  user?: { id: number; login: string; role: 'owner' | 'moderator' }
  channel?: {
    id: number
    display_name: string | null
    eventsub_status: string
    needs_reauth: boolean
  }
  can_action?: boolean
}

export interface Flag {
  id: number
  twitch_message_id: string
  author_login: string
  author_id: string
  message_text: string
  rule_name: string
  rule_version: number
  severity: 'low' | 'medium' | 'high'
  confidence: number
  reason: string
  action_hint: string | null
  status: 'new' | 'reviewed' | 'dismissed' | 'actioned'
  created_at: string
}

export interface Rule {
  name: string
  version: number
  title: string
  severity: string
  confidence_threshold: number
  action_hint: string | null
  languages: string[] | null
  enabled: boolean
  is_current: boolean
  md_content: string
  created_at: string
}

export interface RuleValidation {
  valid: boolean
  frontmatter?: Record<string, unknown>
  errors?: { field: string; message: string }[]
}

export interface BackendSettings {
  type: 'api' | 'cli' | null
  vendor: string | null
  cli_tool: string | null
  model: string | null
  has_api_key: boolean
}

export interface ChannelSettings {
  backend: BackendSettings
  action_proxy_enabled: boolean
  required_action_scopes: string[]
  granted_scopes: string[]
}

export interface Dashboard {
  channel: {
    id: number
    display_name: string
    eventsub_status: string
    needs_reauth: boolean
    action_proxy_enabled: boolean
  }
  backend: { type: string | null; vendor: string | null; cli_tool: string | null; configured: boolean }
  today: Counters
  total: Counters & { cost_usd: number }
  latency_ms: { p50: number | null; p95: number | null; samples: number }
  backlog: number
  precision: {
    rule_name: string
    flags_total: number
    dismissed: number
    confirmed: number
    precision: number | null
  }[]
  recent_messages: { author_login: string; text: string; ts_ms: string }[]
}

export interface Counters {
  messages_processed: number
  flags_created: number
  classification_failed: number
  tokens: number
  requests: number
}

export interface StreamEvent {
  type: 'snapshot' | 'flag.created' | 'flag.updated' | 'channel.status' | 'chat.message'
  data: unknown
}

export interface Moderators {
  moderators: { login: string; registered: boolean }[]
}
