# 🛡 TwitchGuard

ИИ-ассистированная модерация чата Twitch в **advisory-режиме**: система в реальном времени читает чат
через EventSub, классифицирует сообщения LLM по загруженным markdown-правилам и создаёт **флаги** для
живых модераторов. Сама она никого не наказывает — решение (просмотрено / ложное срабатывание /
действие) всегда принимает человек.

Реализация по SRS TwitchGuard v1.0 (идентификаторы FR/NFR/DR/IR из SRS встречаются в коде и тестах).

```
Владелец/Модератор ⇄ React SPA ⇄ FastAPI backend ⇄ Twitch (OAuth / EventSub WS / Helix)
                                        │
                     PostgreSQL (правила, флаги, аудит, usage)
                     Redis (очередь сообщений, кэш, sessions, rate-limit)
                                        │
                         LLM: Anthropic | OpenAI | DeepSeek (API)
                              или локальный CLI: claude | gemini | codex
```

## Возможности

- **Подключение канала только через Twitch OAuth** (Authorization Code Flow + `state`); форм пароля/2FA
  в системе не существует (FR-04, NFR-Sec-01, AR-01).
- **Ingest**: EventSub WebSocket `channel.chat.message` → Redis Stream, дедупликация по `message_id`,
  авто-реконнект с backoff (FR-11..FR-15).
- **Правила** — markdown с YAML-frontmatter (`name`, `title`, `enabled`, `severity`,
  `confidence_threshold`, опционально `action_hint`, `languages`): загрузка drag-drop с предпросмотром,
  валидация с указанием поля, версии, hot-reload без перезапуска (FR-16..FR-22).
- **Классификатор**: батчинг, кэш идентичных сообщений, строгий JSON-вердикт с ретраями и
  корректирующей инструкцией, пороги уверенности per-rule, учёт языка, backoff при 429; при недоступности
  LLM сообщения копятся в Redis и не теряются (FR-23..FR-31, NFR-Rel-03).
- **Очередь флагов**: живая WebSocket-лента + снапшот при переподключении, фильтры, конечный автомат
  статусов `new → reviewed|dismissed|actioned` с 409 на недопустимые переходы, полный аудит (FR-32..FR-39,
  FR-51..FR-53).
- **Action Proxy (опция)**: модератор кнопкой применяет удаление/таймаут/бан через Helix **под своим
  токеном** (не ботом), со scope-проверкой, идемпотентностью и аудитом (FR-40..FR-43, FR-54..FR-56).
- **Наблюдаемость**: дашборд со статусами, счётчиками, p50/p95 задержки, отставанием очереди,
  precision по правилам (датасет ложных срабатываний) и стоимостью (FR-36, FR-49).
- **Безопасность**: токены и API-ключи шифруются at-rest (Fernet, ключ из env), секреты редактируются
  в логах, RBAC на каждом эндпоинте, rate-limit, httpOnly/secure/SameSite cookie (NFR-Sec-01..06).

## Быстрый старт (docker-compose)

### 1. Зарегистрируйте приложение в Twitch Developer Console

1. https://dev.twitch.tv/console/apps → **Register Your Application**.
2. Name — любое; **OAuth Redirect URLs** — `http://localhost:5173/auth/twitch/callback`;
   Category — Website Integration; Client Type — **Confidential**.
3. Скопируйте **Client ID** и сгенерируйте **Client Secret**.

### 2. Настройте окружение

```bash
cd twitchguard
cp .env.example .env
# заполните TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # → ENCRYPTION_KEY
openssl rand -hex 32                                                                        # → SESSION_SECRET
```

### 3. Запустите

```bash
docker compose up --build
```

- Админка: http://localhost:5173 → «Войти через Twitch».
- API/OpenAPI: http://localhost:8000/docs.

После входа: **Настройки → Backend классификации** (API-ключ Anthropic/OpenAI/DeepSeek или локальный
CLI), затем пишите в чат канала — нарушения появятся во вкладке «Флаги» в реальном времени.

## Запуск для разработки (без Docker)

```bash
# инфраструктура
docker compose up -d db redis

# backend
cd backend
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp config.example.yaml config.yaml
set -a; source ../.env; set +a
DATABASE_URL=postgresql+asyncpg://twitchguard:twitchguard@localhost:5432/twitchguard \
SESSION_COOKIE_SECURE=false FRONTEND_ORIGIN=http://localhost:5173 \
  .venv/bin/alembic upgrade head
... (те же переменные) .venv/bin/uvicorn twitchguard.main:app --reload

# frontend (проксирует /auth и /channels на :8000, включая WebSocket)
cd frontend && npm install && npm run dev
```

### Тесты / линт / типы

```bash
cd backend
.venv/bin/pytest -q          # 51 тест: OAuth+state, RBAC, автомат флагов, классификатор,
                             # кэш/дедуп/ретраи, Action Proxy, WS, security log-grep
.venv/bin/ruff check src tests
.venv/bin/mypy src
cd ../frontend && npm run lint && npm run build
```

Тесты не требуют внешних сервисов: SQLite in-memory + fakeredis + мок Twitch/LLM HTTP-слоя.

## Конфигурация

- **Секреты — только окружение** (IR-26): `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`,
  `TWITCH_REDIRECT_URI`, `ENCRYPTION_KEY`, `DATABASE_URL`, `REDIS_URL`, `SESSION_SECRET`
  (+ деплойные `FRONTEND_ORIGIN`, `SESSION_COOKIE_SECURE`, `CONFIG_FILE`).
- **Прикладные параметры** (IR-27) — `backend/config.example.yaml` (валидируется pydantic-settings):
  размер/окно батча, `max_retries`, `cli_timeout_s`, TTL кэша, `redis_stream_maxlen`,
  `reconnect_max_backoff_s`, TTL сессий, rate-limit, `cost_per_mtok_usd` для оценки стоимости.

## Структура

```
twitchguard/
├── backend/
│   ├── src/twitchguard/
│   │   ├── api/               # роутеры §9: auth, rules, flags, settings, moderators, dashboard, WS
│   │   ├── twitch/            # ВЕСЬ Twitch-специфичный код: oauth, helix, eventsub (NFR-Main-01)
│   │   ├── moderation/        # промпт, вердикты, engine, backends/ (anthropic|openai|deepseek|cli)
│   │   ├── rules/             # парсер frontmatter, версии, встроенные правила
│   │   ├── flags.py           # конечный автомат §8, precision
│   │   ├── actions.py         # Action Proxy §10
│   │   ├── ingest.py ws.py pipelines.py supervisor.py crypto.py rbac.py sessions.py …
│   ├── rules_builtin/         # встроенные spam.md, toxicity.md
│   ├── migrations/            # Alembic (схема §7)
│   └── tests/                 # критерии приёмки §14
├── frontend/                  # React 18 + TS: Login, Dashboard, Flags, Rules, Settings
├── docker-compose.yml         # postgres 15 + redis 7 + backend + frontend
└── .env.example
```

## Как это соответствует правилам Twitch (NFR-Main-03/04)

- **Никаких паролей и 2FA.** Единственный путь авторизации — официальный OAuth Authorization Code
  Flow; учётные данные вводятся только на страницах Twitch. В коде нет ни одного эндпоинта или поля,
  принимающего пароль (проверяется тестом по OpenAPI-схеме).
- **Система никогда не пишет в чат.** В Helix-клиенте отсутствует метод отправки сообщений; никакой
  имитации зрителей, накрутки и «реплик от бота» (AR-02, AR-03). Читающая учётная запись одна.
- **Никаких автоматических наказаний.** Базовый режим — advisory: ИИ только помечает. Опция Action
  Proxy выполняет действие исключительно по явному клику человека-модератора, через официальный Helix
  API, **под токеном этого модератора**, с записью в аудит (AR-04).
- **Минимальные scope.** При подключении запрашиваются только `user:read:chat` + `channel:bot`;
  модерационные scope (`moderator:manage:banned_users`, `moderator:manage:chat_messages`) — только при
  явном включении Action Proxy владельцем.
- **Приватность данных.** Сырой поток чата на диск не персистится: буфер живёт в Redis без
  персистентности (в compose Redis запущен с выключенными RDB/AOF); в PostgreSQL попадают только тексты
  помеченных сообщений в составе флагов (DR-10, AR-06). «Отключить канал» отзывает токены на стороне
  Twitch и стирает их из БД.
- **Токены и ключи шифруются at-rest** и не появляются в логах и ответах API (тест-«греп» по логам —
  в CI).

> **AR-07.** Имена scope, версия подписки EventSub (`channel.chat.message` v1) и форматы полезной
> нагрузки Helix соответствуют официальной документации Twitch на момент реализации. Внешний API
> периодически меняется — при обновлении сверяйтесь с https://dev.twitch.tv/docs.

## Известные ограничения

- Продукт «один канал — один владелец»: пользователь либо владеет своим каналом, либо приглашён
  модератором на один канал.
- Удаление модератора не отзывает его уже выданную сессию мгновенно (истекает по TTL); правки правил
  он в любом случае делать не может (RBAC проверяется на каждом запросе).
- `usage.tokens` для CLI-backends равен 0 — CLI-инструменты не сообщают расход токенов.
