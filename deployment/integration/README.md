# ABC4RD pilot synchronization

`abc4rd-pilot-sync.py` is the minimal idempotent bridge for the first three
pilot accounts. It reads only users authenticated through the configured Open
edX Keycloak provider whose usernames start with `pilot_`.

For every active enrollment it:

1. reuses or creates the opaque Academy Core identity;
2. reuses or records the course entitlement and the current result event;
3. creates the ERPNext participant as `candidate`, then projects the verified
   active enrollment as `learner`;
4. writes a non-PII state projection for the learner portal;
5. exposes a technical-pilot certificate only for a passing Open edX grade;
6. verifies the Academy Core hash chain before reporting success.

The timer makes retries safe through Core idempotency and CRM existence checks.
It does not copy email, full name, phone, passwords, assignment text or messages.
Payments and AI review are outside this bridge.

