# Academy Core deployment

This compose project runs Academy Core on the existing Tutor network. It does
not publish a host port. Tutor/Caddy exposes only signed provider webhooks and
the checkout-result redirects; NOWPayments invoice and Lemon Squeezy checkout
creation stay internal and require
`Authorization: Bearer <ABC4RD_CHECKOUT_TOKEN>`.

Sandbox is the default. Do not add `--nowpayments-live` until the legal payee,
treasury wallet, provider account, refund policy, and reconciliation gate have
all been approved.

Server location: `/opt/abc4rd/academy-core`. Create `.env` with mode `600` and
source every secret from 1Password rather than Git; `.env.example` only lists
the required names.

To start the Core before provider onboarding, create an empty mode-`600` `.env`.
Provider routes then remain absent. Add NOWPayments values together only after
its sandbox API key exists. For Lemon Squeezy, use a Test-mode API key, USD
Store, one-time Variant, a 6–40 character signing secret, and the same internal
checkout token. Live mode remains forbidden until Store activation, KYB/KYC,
payout, policy and reconciliation gates are documented.
