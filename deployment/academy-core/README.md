# Academy Core deployment

This compose project runs Academy Core on the existing Tutor network. It does
not publish a host port. Tutor/Caddy exposes only the signed NOWPayments webhook
and the checkout-result redirects; invoice creation stays internal and requires
`Authorization: Bearer <ABC4RD_CHECKOUT_TOKEN>`.

Sandbox is the default. Do not add `--nowpayments-live` until the legal payee,
treasury wallet, provider account, refund policy, and reconciliation gate have
all been approved.

Server location: `/opt/abc4rd/academy-core`. Create `.env` with mode `600` and
source every secret from 1Password rather than Git; `.env.example` only lists
the required names.

To start the Core before provider onboarding, create an empty mode-`600` `.env`.
The invoice and webhook routes then remain absent. Add all three values from
1Password together only after the NOWPayments sandbox API key exists.
