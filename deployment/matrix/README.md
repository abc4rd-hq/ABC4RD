# ABC4RD Messenger

Synapse is the replaceable Matrix homeserver and Element Web is the learner UI.
Passwords and public account registration are disabled. Users enter through the
same Keycloak realm with OIDC. The existing `pilot_001` room owner invites new
pilot accounts automatically into the invite-only pilot room alias.

MatrixRTC calls use the self-hosted LiveKit SFU and MatrixRTC authorization
service. Browser and mobile clients discover it through Synapse and the Element
Web configuration. Signalling is reverse-proxied under `matrix.abc4rd.org`; media
uses TCP 7881 or UDP 7882. Screen sharing is provided by the same call stack.
Install `config/90-abc4rd-livekit.conf` into `/etc/sysctl.d/` on the host and
apply it with `sysctl --system` so the SFU can allocate adequate UDP buffers.

Because the public apex `abc4rd.org` is hosted separately, the authorization
container resolves that name to the local Caddy proxy. Caddy serves Matrix
server delegation on an internal-CA TLS site and the container trusts only the
mounted Caddy root certificate for that lookup. Public DNS and the apex website
remain unchanged. On a fresh host, apply and start the Tutor Caddy configuration
once before `docker compose up`; this creates the mounted local root certificate.

Uploads are capped at 25 MB per file. URL previews are enabled with private,
loopback, documentation and multicast networks blocked from the preview fetcher.

The deployment is deliberately single-node for the three-person pilot. Its
PostgreSQL, media and the homeserver signing key are included in the encrypted
runtime bundle created by `deployment/backup/create-runtime-backup.sh`. The
copy is not complete until it is moved off-host and its SHA-256 is verified.
Federation is disabled for the pilot and can be reviewed separately later.

The pilot room intentionally remains invite-only and unencrypted so future
ABC4RD automation can process its events. Private encrypted rooms can still be
created by users, but every device must then configure secure key recovery.

Render `config/homeserver.yaml` from `.env` using `render-config.py`; never
commit the rendered file or `.env`.
