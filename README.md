# ABC4RD Academy

Open-source platform components for an AI-native international academy,
library and project ecosystem.

[Website](https://abc4rd.org) · [Academy](https://learn.abc4rd.org) ·
[Knowledge base](https://pluriverse.win/knowledge/kb-098-abc4rd/s047-008-0194-portable-open-source-ecosystem/)

## What is in this repository

- `academy-core/` — minimal learner, entitlement, payment-state, AI-review,
  credential and audit-event domain model;
- `digital-twin/` — deterministic end-to-end simulations for three pilot
  participants, payment controls, appeals, rewards and reconciliation;
- `deployment/openedx/` — reproducible Open edX deployment and verification tools;
- `deployment/keycloak/` — isolated identity service with PostgreSQL and safe
  bootstrap handling;
- `deployment/erpnext/` — CRM deployment scaffolding and ABC4RD learner records.

Course content, books, learner data, production secrets, operational incident
reports and backups are deliberately excluded from this public repository.

## Current target

Prove one complete and auditable route for three pilot learners:

`ABC4RD ID → enrollment → $1 payment → course → AI review → credential → CRM → audit trail`

Payments remain gated until the recipient, provider and live-settlement controls
are verified. The included models and tests do not create real charges.

## Local verification

```bash
cd academy-core
python3 -m unittest discover -s tests -v

cd ..
python3 -m unittest discover -s digital-twin/tests -v

cd deployment/erpnext
./scripts/check-static.sh
```

## Security

Never commit credentials, learner data, payment secrets or production exports.
See [SECURITY.md](SECURITY.md) for responsible disclosure.

## Contributing

Contributions are welcome through issues and pull requests. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

Released under the [MIT License](LICENSE).
