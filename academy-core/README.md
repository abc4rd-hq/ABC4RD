# ABC4RD Academy Core — минимальный каркас

Изолированный каркас собственного Control Plane ABC4RD. Он координирует один
проверяемый маршрут пилота, но не заменяет Open edX, Keycloak, ERPNext, Matrix,
S3 или платёжного провайдера.

Статус: `PROTOTYPE / NO PAYMENT SETTLEMENT`. Каркас не подключён к VPS и
внешним системам. По умолчанию разрешены только sandbox-записи.

## Граница ответственности

| Данные | Источник истины | Что хранит Core |
|---|---|---|
| Вход, учётная запись, роли | Keycloak | Непрозрачную ссылку на IAM subject и собственный UUID `abc4rd_id` |
| Курсы, задания, оценки, прогресс | Open edX | Ссылки и минимальные интеграционные события, но не копию учебных данных |
| CRM, задачи, обращения | ERPNext | Только ссылки/события сквозного маршрута |
| Сообщения и комнаты | Matrix | Только ссылки/события, если они нужны аудиту |
| Файлы и доказательства | S3-совместимое хранилище | Ссылку и, где применимо, SHA-256 |
| Факт движения денег | Платёжный провайдер | Append-only shadow ledger наблюдений; Core никогда не выполняет charge |
| ABC4RD ID, согласия, entitlement, AI review, credentials, audit/outbox | Academy Core | Канонические координационные факты |

В Core намеренно нет ФИО, email, паролей, Keycloak-ролей, CRM-профиля, текстов
курса, оценок, сообщений, бинарных credential-файлов или платёжных секретов.
Внешние ссылки должны быть непрозрачными идентификаторами, а не PII.

## Что реализовано

- UUID как внутренний непрозрачный `abc4rd_id`; внешний/публичный формат ещё не утверждён.
- Append-only действия согласия `GRANTED` / `WITHDRAWN`.
- Append-only решения entitlement `GRANTED` / `REVOKED` по внешнему ресурсу.
- Универсальные минимальные domain events со ссылкой на внешний агрегат.
- Утверждённая целевая цена пилота: `USD 1.00`, то есть `amount_minor=100`.
- Payment shadow ledger различает `ATTEMPT`, подтверждённый charge и refund;
  попытка всегда имеет `recognized_charge=false`.
- Добавлен sandbox-first адаптер NOWPayments: фиксированный invoice на `USD 1.00`,
  HMAC-SHA512-проверка IPN, отбрасывание промежуточных статусов и идемпотентная
  запись только `finished`/`refunded` в shadow ledger.
- Будущий режим `LIVE` моделируется схемой, но по умолчанию заблокирован. Для
  записи live-наблюдений одновременно нужны явные provider и gate reference.
- AI-first review: решение содержит `reviewer_agent_id`, модель и версию.
- Independent review/appeal требует другого `reviewer_agent_id` относительно
  предыдущего решения.
- Append-only oversight outbox для unresolved, high-risk и adverse review;
  адрес — `TBD_OVERSIGHT_MAILBOX`, отправка не реализована.
- Реестр выданных credentials по ссылке и SHA-256; регистрация требует решения
  `APPROVED` с полной идентификацией AI reviewer.
- Append-only audit trail с SHA-256 hash chain.
- Глобальная идемпотентность всех write-запросов.
- SQLite-триггеры, запрещающие `UPDATE`/`DELETE` канонических фактов.

## Явные placeholders Pilot Charter

До решения владельца продукта код не фиксирует:

- окончательный пилотный курс (курс `0009` остаётся кандидатом);
- реальные личности трёх слушателей и утверждённый способ получения согласий;
- каталог/тексты согласий и срок их действия;
- правила выдачи и отзыва entitlement;
- legal entity, страну, налоги, комиссии и переход к реальным деньгам;
- платёжного провайдера, типы его событий и правила reconciliation/refund;
- oversight mailbox, правила эскалации и полную authority matrix;
- критерии итоговой оценки и выпуска;
- credential issuer, формат, signing keys, revocation registry и public verifier.

Строковые поля вроде `consent_type`, `resource_type`, `entry_type`, `review_kind`
и `format` поэтому принимают значения интеграционного адаптера. Значения вида
`*_PLACEHOLDER` в тестах не являются бизнес-каноном.

## Платёжный инвариант

`POST /v1/payment-ledger` только регистрирует внешний или синтетический факт. В
Core нет операции создания, подтверждения или списания платежа.

- `ATTEMPT` — попытка, никогда не charge (`recognized_charge=false`).
- `PROVIDER_CONFIRMED_CHARGE` — только наблюдение с обязательным
  `provider_evidence_ref`.
- `PROVIDER_CONFIRMED_REFUND` — только наблюдение с обязательным
  `provider_evidence_ref`.

Для всех новых записей пилота сервис требует `amount_minor=100` и `currency=USD`.
`LIVE` по умолчанию отклоняется. Флаги запуска `--live-payment-provider` и
`--live-payment-gate-ref` лишь разрешают моделировать live-наблюдения конкретного
провайдера; `settlement_capability` всегда остаётся `false`.
Старые V1-записи при миграции консервативно получают `fact_type=ATTEMPT`, поэтому
не превращаются задним числом в подтверждённые списания.

### Криптоплатёжный адаптер

`academy_core.payments.nowpayments` по умолчанию использует sandbox API. Переход
на LIVE требует одновременных `sandbox=False` и `allow_live=True`; это защищает
от случайной отправки запроса в боевой API. API key и IPN secret передаются
только средой исполнения или secret manager и не записываются в Core.

```python
from academy_core.payments import NowPaymentsClient

client = NowPaymentsClient(api_key="read-from-secret-manager")
invoice = client.create_pilot_invoice(
    order_id="opaque-order-uuid",
    ipn_callback_url="https://payments.abc4rd.org/v1/nowpayments/ipn",
    success_url="https://learn.abc4rd.org/payment/success",
    cancel_url="https://learn.abc4rd.org/payment/cancel",
)
```

Публичный webhook endpoint намеренно пока не включён: до него нужны DNS/TLS,
service authentication для Core и sandbox credentials. Функция `process_ipn`
уже проверяет `x-nowpayments-sig`, цену и валюту до любой записи, а повтор
одинакового callback не создаёт второй финансовый факт.

## AI-first review и oversight outbox

Первичное решение принимает актор `AI_AGENT`; `actor_ref` должен совпадать с
`reviewer_agent_id`, а модель и версия обязательны. Для `INDEPENDENT_REVIEW` и
`APPEAL` нужна ссылка на предыдущее решение и новый независимый
`reviewer_agent_id`. Одобренное AI-решение может быть основанием регистрации
credential.

При открытии любого пока unresolved case создаётся `REVIEW_UNRESOLVED`; для
`risk_level=HIGH` добавляется причина `HIGH_RISK`. Решения `REJECTED` и
`CHANGES_REQUESTED` создают `REVIEW_ADVERSE_DECISION`. Все записи имеют
`delivery_status=PENDING_CONFIGURATION`: mailbox и отправщик отсутствуют.

## Запуск

Нужен Python `3.9+`; runtime-зависимостей вне стандартной библиотеки нет.

```bash
cd /Users/dom/Documents/ABC4RD/academy-core
python3 -m academy_core init --db var/academy-core.db
python3 -m academy_core serve --db var/academy-core.db --host 127.0.0.1 --port 8080
```

Будущую live-модель можно включить только явной парой параметров (это не включает
реальный charge или отправку запросов провайдеру):

```bash
python3 -m academy_core serve --db var/academy-core.db \
  --live-payment-provider FUTURE_PROVIDER_PLACEHOLDER \
  --live-payment-gate-ref GATE_APPROVAL_PLACEHOLDER
```

Сервис по умолчанию слушает только loopback. Пример smoke check:

```bash
curl -s http://127.0.0.1:8080/health

curl -sS -X POST http://127.0.0.1:8080/v1/identities \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: local-example-identity-1' \
  -d '{
    "external_identity_ref":"keycloak:opaque-subject-placeholder",
    "actor_type":"SYSTEM",
    "actor_ref":"local-smoke-test"
  }'
```

Все `POST` требуют уникальный `Idempotency-Key`. Повтор идентичного запроса
возвращает прежний результат; повтор ключа с другим телом получает `409`.

## HTTP API

| Метод и путь | Назначение |
|---|---|
| `GET /health` | Режим сервиса и проверка audit chain |
| `POST /v1/identities` | Создать ABC4RD ID для opaque IAM subject |
| `POST /v1/consents` | Записать действие согласия |
| `POST /v1/entitlements` | Записать решение доступа |
| `POST /v1/events` | Принять минимальный интеграционный факт |
| `POST /v1/payment-ledger` | Записать attempt или подтверждённое провайдером наблюдение |
| `POST /v1/reviews` | Открыть AI-first review case |
| `POST /v1/review-decisions` | Записать версионированное решение AI reviewer |
| `POST /v1/credentials` | Зарегистрировать credential после AI approval |
| `GET /v1/oversight-outbox?limit=100` | Прочитать неотправляемые oversight-события |
| `GET /v1/audit?limit=100` | Прочитать последние записи аудита |
| `GET /v1/audit/verify` | Пересчитать hash chain |

API строгий: неизвестные поля отклоняются. Это в том числе не позволяет случайно
передать `email`/`name` в endpoint создания идентификатора.

## Схема данных

Канонические миграции:
[`001_initial.sql`](academy_core/schema/001_initial.sql) и
[`002_owner_override_ai_review.sql`](academy_core/schema/002_owner_override_ai_review.sql).

```text
abc4rd_identities
  ├── consent_records[]
  ├── entitlement_records[]
  ├── payment_ledger_entries[]
  └── credential_records[] ──> review_decisions[] ──> review_cases
                                                        └── oversight_outbox_events[]

pilot_price_targets[]  domain_events[]  idempotency_keys[]  audit_entries[]
```

Исправления представлены новыми фактами (`WITHDRAWN`, `REVOKED`, новое решение),
а не изменением истории. SQLite подходит для минимального локального каркаса;
выбор production-СУБД в Pilot Charter не утверждён.

## Проверка

```bash
cd /Users/dom/Documents/ABC4RD/academy-core
python3 -m unittest discover -s tests -v
python3 -m academy_core verify-audit --db var/academy-core.db
```

Тесты проверяют цену `USD 1.00`, различие attempt/charge, default-deny для LIVE,
явную будущую LIVE-конфигурацию без settlement, AI identity/model/version,
независимость appeal agent, oversight outbox, credential approval,
идемпотентность, append-only audit chain, строгий HTTP payload, sandbox invoice,
IPN signature, duplicate webhook и refund.

## Что нужно до интеграции или production

Этот этап сознательно не реализует deployment. До любого внешнего подключения
нужны отдельные утверждённые решения и тесты:

1. сервисная аутентификация/авторизация (например, проверяемые Keycloak tokens),
   mTLS/network policy и секреты вне репозитория;
2. утверждённые event contracts и адаптеры Open edX/Keycloak/ERPNext/Matrix;
3. transaction delivery, реальный outbox sender, reconciliation и проверка
   webhook signatures;
4. privacy retention/erasure policy без разрушения обязательного аудита;
5. подписанное/WORM-хранилище audit evidence и резервное восстановление;
6. credential signing, verifier и revocation после выбора стандарта/issuer;
7. нагрузочная, security и migration-проверка выбранной production-СУБД.

Ни один из этих пунктов не должен считаться выполненным на основании данного
каркаса.
