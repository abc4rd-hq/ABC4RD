# ABC4RD tokenomics digital twin

Локальная модель этапа 3 для трёх синтетических участников. Каноническая цена
курса — `USD 1.00`, то есть `minor=100`. Все записи остаются единицами симуляции
`SIM`: модель не списывает и не возвращает реальные деньги.

## Жёсткая граница безопасности

- `simulation_only=true` обязателен для каждого запуска сценария.
- Нет HTTP-клиента, платёжного SDK, почтового транспорта, кошелька или blockchain
  RPC; итог всегда содержит `network_actions=0` и `real_money_transactions=0`.
- Участники — только `SYNTH-001..003`; персональных данных нет.
- Notification — локальная запись для `TBD_MAILBOX` с
  `delivery_status=not_sent`; режим отправки запрещён валидацией.
- Состояние существует только в памяти процесса и не затрагивает production.

## Сценарии

`scenarios/three-participant.json` отражает текущее положение: все реальные
payment gates остаются незакрытыми, поэтому readiness будущего списания
блокируется. При этом локально проверяются sandbox payment, decline, refund,
scholarship, reward, treasury, reconciliation, abuse и emergency pause.

`scenarios/future-dollar-after-gates.json` — контрфактический переход. Только
после всех семи синтетически выставленных gates модель:

1. признаёт готовность будущего human-authorized charge `USD 1.00`;
2. имитирует charge `minor=100` без внешней транзакции;
3. имитирует полный refund `minor=100`;
4. сверяет gross `100`, refund `100` и net `0`.

Обязательные gates: утверждённый юридический получатель, проверенный provider,
успешные sandbox charge и refund, проверенная webhook idempotency, успешная
reconciliation и human go-live approval. Истинные значения этих gates должны
появиться из внешних доказательств; twin сам их не подтверждает.

## AI review и уведомление

Высокорисковый abuse-case требует цепочку до заморозки участника:

1. `ai_primary_review` с отдельными `review_id` и `reviewer_id`;
2. `ai_second_reconsideration` с другим `reviewer_id`, ссылкой на primary review
   и `independent=true`;
3. локальный `notification` event для `TBD_MAILBOX`, если второй AI подтвердил
   высокий риск или оставил case unresolved;
4. только затем `abuse` freeze.

## Воспроизводимый запуск

Из корня репозитория:

```bash
python3 digital-twin/run.py
python3 digital-twin/run.py --json
python3 digital-twin/run.py digital-twin/scenarios/future-dollar-after-gates.json
python3 -m unittest discover -s digital-twin/tests -v
```

Используется только standard library Python 3.9+. Поле `expect` превращает
ожидаемый outcome каждого события в исполняемую проверку.

## Сохранённые правила

- decline не меняет доступ или treasury;
- защитный полный refund разрешён во время emergency pause;
- scholarship и reward расходуют только доступный резерв;
- frozen participant и emergency pause блокируют новые value-события;
- точный повтор `event id` идемпотентен, а изменённый payload блокируется;
- treasury conservation проверяется после каждого события.

Это исследовательская модель, а не бухгалтерия, платёжная система, smart
contract, AI-решение с юридической силой или утверждённая токеномика.
