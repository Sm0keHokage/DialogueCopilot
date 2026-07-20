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

## Как передаются звук и картинка трансляции

**Никак — и это принципиально.** В системе нет Playwright, Selenium и любой другой браузерной
автоматизации или парсеров:

- **Чат** приходит официальным **Twitch EventSub WebSocket** (`channel.chat.message`) — это текстовые
  JSON-события от самого Twitch, задокументированный API, а не скрейпинг страницы.
- **Видео и звук** TwitchGuard не проксирует и не перекодирует вообще. В дашборд встроен
  **официальный Twitch-плеер** (iframe `player.twitch.tv`): медиапоток идёт напрямую с CDN Twitch в
  браузер модератора. Наш backend не касается ни одного байта аудио/видео — он работает только с
  текстом чата.
- **Действия модерации** — официальный **Helix API** под токеном живого модератора.

Это одновременно и легально (соответствует Twitch Developer Services Agreement — парсинг и обход
плеера запрещены), и дешевле/надёжнее: не нужны headless-браузеры и ре-стриминговая инфраструктура.

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
- **Параллельные ИИ-агенты** для чатов с большим онлайном: владелец задаёт число агентов на канал
  (Настройки → «Параллельные ИИ-агенты», до `classifier.max_workers`); Redis consumer group шардирует
  поток между агентами, каждый делает свои LLM-вызовы конкурентно, зависшие у упавшего агента
  сообщения перехватываются соседним (`XAUTOCLAIM`). Twitch-аккаунт при этом остаётся **один** —
  несколько аккаунтов на канал запрещены правилами Twitch и SRS (AR-03); масштабируется только
  классификация, которая и является узким местом.
- **Личный кабинет**: регистрация почта+ник+пароль с подтверждением по email, вход по почте или нику,
  привязка Twitch-канала через OAuth из кабинета, смена пароля, «выйти на всех устройствах», тариф
  «Бесплатно · бета» со всеми фишками.
- **Безопасность**: токены и API-ключи шифруются at-rest (Fernet, ключ из env), пароли — scrypt с
  солью, ссылки подтверждения одноразовые с TTL и хранятся хэшем, анти-брутфорс (лок на 15 минут после
  5 неудачных попыток), защита от перечисления почт, регенерация сессии при входе, инвалидация чужих
  сессий при смене пароля, секреты редактируются в логах, RBAC на каждом эндпоинте, rate-limit,
  httpOnly/secure/SameSite cookie, security-заголовки и CSP (NFR-Sec-01..06).

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
.venv/bin/pytest -q          # 71 тест: OAuth+state, личный кабинет (регистрация/подтверждение/
                             # брутфорс-лок), RBAC, автомат флагов, классификатор и пул ИИ-агентов,
                             # кэш/дедуп/ретраи, Action Proxy, WS, security log-grep
.venv/bin/ruff check src tests
.venv/bin/mypy src
cd ../frontend && npm run lint && npm run build
```

Тесты не требуют внешних сервисов: SQLite in-memory + fakeredis + мок Twitch/LLM HTTP-слоя.

## Конфигурация

- **Секреты — только окружение** (IR-26): `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`,
  `TWITCH_REDIRECT_URI`, `ENCRYPTION_KEY`, `DATABASE_URL`, `REDIS_URL`, `SESSION_SECRET`
  (+ деплойные `FRONTEND_ORIGIN`, `SESSION_COOKIE_SECURE`, `CONFIG_FILE`; почта кабинета —
  `SMTP_HOST/PORT/USER/PASSWORD/FROM/STARTTLS`, `PUBLIC_BASE_URL`; без SMTP_HOST ссылка
  подтверждения пишется в лог — удобно для разработки).
- **Прикладные параметры** (IR-27) — `backend/config.example.yaml` (валидируется pydantic-settings):
  размер/окно батча, `max_retries`, `cli_timeout_s`, TTL кэша, `redis_stream_maxlen`,
  `reconnect_max_backoff_s`, TTL сессий, rate-limit, `cost_per_mtok_usd` для оценки стоимости.

## Структура

```
twitchguard/
├── backend/
│   ├── src/twitchguard/
│   │   ├── api/               # роутеры §9: auth, account, rules, flags, settings, moderators, dashboard, WS
│   │   ├── twitch/            # ВЕСЬ Twitch-специфичный код: oauth, helix, eventsub (NFR-Main-01)
│   │   ├── moderation/        # промпт, вердикты, engine (пул ИИ-агентов), backends/
│   │   ├── rules/             # парсер frontmatter, версии, встроенные правила
│   │   ├── flags.py           # конечный автомат §8, precision
│   │   ├── actions.py         # Action Proxy §10
│   │   ├── accounts.py        # личный кабинет: scrypt-пароли, токены подтверждения
│   │   ├── emailer.py         # отправка писем (SMTP или dev-режим в лог)
│   │   ├── ingest.py ws.py pipelines.py supervisor.py crypto.py rbac.py sessions.py …
│   ├── rules_builtin/         # встроенные spam.md, toxicity.md
│   ├── migrations/            # Alembic (схема §7)
│   └── tests/                 # критерии приёмки §14
├── frontend/                  # React 18 + TS: Login, Dashboard, Flags, Rules, Settings
├── docker-compose.yml         # postgres 15 + redis 7 + backend + frontend
└── .env.example
```

## Как это соответствует правилам Twitch (NFR-Main-03/04)

- **Никаких паролей и 2FA от Twitch.** Подключение канала — только официальный OAuth Authorization
  Code Flow; учётные данные Twitch вводятся только на страницах Twitch. Пароль личного кабинета
  TwitchGuard — отдельная локальная сущность (эндпоинты `/account/*`), к Twitch-аккаунту отношения не
  имеет; тест по OpenAPI-схеме гарантирует, что вне `/account/*` полей с паролем нет. Это осознанное
  расширение исходного AR-01 по решению владельца продукта: смысл требования — «не собирать учётные
  данные Twitch» — сохранён.
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
