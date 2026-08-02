# ABC4RD Messenger

Synapse is the replaceable Matrix homeserver and Element Web is the learner UI.
Passwords and public account registration are disabled. Users enter through the
same Keycloak realm with OIDC. The existing `pilot_001` room owner invites new
pilot accounts automatically into the invite-only pilot room alias.

The deployment is deliberately single-node for the three-person pilot. Its
PostgreSQL database and media directory must be covered by the server backup.
Federation is disabled for the pilot and can be reviewed separately later.

Render `config/homeserver.yaml` from `.env` using `render-config.py`; never
commit the rendered file or `.env`.
