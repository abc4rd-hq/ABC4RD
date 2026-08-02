# ABC4RD ERPNext CRM — production preparation

Статус: `DEPLOYED / RUNTIME VERIFIED`

Дата сверки: `2026-08-02`

Production-развёртывание выполнено на VPS `vps-11b90ce5` после проверки Keycloak,
Open edX SSO и ресурсов. Публичный адрес: `https://crm.abc4rd.org`.

Readback от `2026-08-02`:

- ERPNext `16.30.0`, Frappe `16.29.0`, `abc4rd_crm` `0.1.0`;
- девять обязательных runtime-сервисов работают, все healthcheck проходят;
- три ABC4RD DocType установлены и прочитаны из production-сайта;
- DNS, TLS, Caddy route и публичный `/api/method/ping` проверены;
- создан штатный логический backup и проверена его зашифрованная off-host копия.

ERPNext пока использует собственный административный вход. Его SSO с Keycloak —
отдельный следующий gate; наличие SSO Open edX нельзя считать SSO ERPNext.

## Что подготовлено

- официальный ERPNext `v16.30.0` с образом, закреплённым по multi-arch digest;
- совместимая стабильная линия Frappe Framework `v16` (`v16.29.0` на дату сверки);
- MariaDB `11.8` и Redis `8.6-alpine`, также закреплённые по digest;
- production Compose без публичных host ports;
- минимальное приложение `abc4rd_crm` с тремя DocTypes;
- секреты только через файловые Docker secrets;
- read-only preflight, локальная статическая проверка, runtime readback и локальный
  штатный логический backup Frappe.

Compose собран по официальной production-схеме
[`frappe_docker v3.2.1`](https://github.com/frappe/frappe_docker/releases/tag/v3.2.1),
а не по одноразовому `pwd.yml`.

## Сущности

1. `ABC4RD Participant` — одна постоянная карточка, имя документа равно
   каноническому UUID `abc4rd_id`.
2. `ABC4RD Inquiry` — обращение, связанное с одной карточкой участника.
3. `ABC4RD Audit Reference` — дочерняя таблица непрозрачных ссылок на события и
   доказательства без payload и PII.

PII допускается только в явно помеченных CRM-полях ERPNext. Academy Core получает
только `abc4rd_id` и непрозрачные ссылки. Оценки, попытки, тексты заданий и прогресс
Open edX в эту модель не входят. Финансовое поле принимает только сверенный
конечный факт или явное `NO_VERIFIED_FACT`.

Полная матрица: [`../../docs/08-ERPNEXT-CRM.md`](../../docs/08-ERPNEXT-CRM.md).

## Локальная проверка

```bash
cd /Users/dom/Documents/ABC4RD/deployment/erpnext
./scripts/check-static.sh
```

Проверка не создаёт контейнеры: компилирует Python, разбирает DocType JSON,
проверяет инварианты схемы и выполняет `docker compose config`, если Compose
доступен.

## Runbook последовательного развёртывания

1. Скопировать этот каталог в `/opt/abc4rd/erpnext`.
2. Создать `/opt/abc4rd/erpnext/.env` из `.env.example`.
3. Создать два случайных секрета в путях из `.env`, права файлов `0600`; мастер-копии
   сохранить в 1Password vault `ABC4RD`.
4. После полной проверки Keycloak и Open edX SSO выполнить read-only gate:

   ```bash
   ERPNEXT_ENV_FILE=/opt/abc4rd/erpnext/.env \
     /opt/abc4rd/erpnext/scripts/preflight.sh
   ```

5. Зафиксировать вывод preflight и решение `GO`. Только затем построить образ и
   поднять инфраструктуру:

   ```bash
   cd /opt/abc4rd/erpnext
   docker compose --env-file .env build --pull
   docker compose --env-file .env up -d db redis-cache redis-queue configurator
   docker compose --env-file .env --profile bootstrap run --rm site-bootstrap
   docker compose --env-file .env up -d
   ```

6. В отдельном изменении Tutor/Caddy направить `crm.abc4rd.org` на
   `abc4rd-erpnext-frontend:8080` по внешней Docker-сети `tutor_local_default`.
   До этого Compose не публикует ERPNext наружу.
7. Выполнить runtime readback:

   ```bash
   ERPNEXT_ENV_FILE=/opt/abc4rd/erpnext/.env \
     /opt/abc4rd/erpnext/scripts/check-ready.sh
   ```

Команды выше остаются runbook для повторного или нового последовательного окна
работ. Фактический production-статус зафиксирован в readback в начале документа.

## Backup

```bash
ERPNEXT_ENV_FILE=/opt/abc4rd/erpnext/.env \
  /opt/abc4rd/erpnext/scripts/backup.sh
```

Скрипт создаёт штатный логический backup БД, public/private files и печатает
SHA-256. Это последовательные операции, а не атомарный snapshot: для строгой
согласованности БД и файлов нужен отдельный maintenance/quiesce window без записи.
Его результат намеренно помечен `LOCAL_BACKUP_ONLY=1`: backup считается готовым
только после шифрования, внешнего копирования и независимой сверки checksum.
Безопасный restore drill и retention описаны в `docs/08-ERPNEXT-CRM.md`.

Для restore drill недостаточно изменить только Compose project name: production
volumes имеют явные имена. Нужно создать отдельный env из
`.env.restore-drill.example`, заменить `YYYYMMDD`, использовать отдельные secret
files и до любого restore получить `PASS`:

```bash
ERPNEXT_ENV_FILE=/opt/abc4rd/erpnext/.env \
  ./scripts/check-restore-isolation.sh /secure/path/restore-drill.env
```

Compose подставит отдельный `ERPNEXT_RESOURCE_PREFIX` во все drill volumes и
networks. Каждый drill-вызов Compose обязан включать оба файла, иначе защита
frontend не действует:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.restore-drill.yml \
  --env-file /secure/path/restore-drill.env \
  config
```

Override выключает frontend, scheduler и workers профилями, делает egress
внутренним и заменяет Tutor network отдельной внутренней drill-сетью. Это не даёт
восстановленным webhooks, email jobs и интеграциям обратиться к production
сервисам до нейтрализации side effects. `docker compose down -v` допустим только с
теми же двумя `-f`, тем же drill env, после повторной проверки prefix и сохранения
evidence.

## Обновление

Нельзя менять `v16.30.0` на плавающий тег. Обновление начинается с нового review:
release notes ERPNext/Frappe, новый digest, полный backup, тестовая миграция и
restore drill. Major downgrade Frappe не поддерживается.
