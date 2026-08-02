"""ABC4RD production settings for Tutor/Open edX."""

from tutor import hooks


hooks.Filters.CONFIG_DEFAULTS.add_items(
    [
        ("ABC4RD_OIDC_CLIENT_SECRET", ""),
    ]
)


hooks.Filters.ENV_PATCHES.add_item(
    (
        "openedx-lms-common-settings",
        "FEATURES['ALLOW_PUBLIC_ACCOUNT_CREATION'] = False",
    )
)


hooks.Filters.ENV_PATCHES.add_item(
    (
        "openedx-lms-common-settings",
        """
SOCIAL_AUTH_OAUTH_SECRETS = {
    **SOCIAL_AUTH_OAUTH_SECRETS,
    "identityServer3": {{ ABC4RD_OIDC_CLIENT_SECRET | tojson }},
}
SOCIAL_AUTH_IDENTITYSERVER3_SCOPE = ["openid", "profile", "email"]
""".strip(),
    )
)


hooks.Filters.ENV_PATCHES.add_item(
    (
        "caddyfile",
        """
id.abc4rd.org{$default_site_port} {
    @blocked path /admin* /realms/master* /metrics* /health*
    respond @blocked 404

    import proxy "abc4rd-keycloak:8080"
}

payments.abc4rd.org{$default_site_port} {
    route {
        @webhook path /v1/payments/nowpayments/ipn
        reverse_proxy @webhook "abc4rd-academy-core:8080" {
            header_up X-Forwarded-Port 443
        }

        @lemonsqueezy_webhook path /v1/payments/lemonsqueezy/webhook
        reverse_proxy @lemonsqueezy_webhook "abc4rd-academy-core:8080" {
            header_up X-Forwarded-Port 443
        }

        @checkout_result path /checkout/success /checkout/cancel
        redir @checkout_result https://learn.abc4rd.org/dashboard 303

        respond 404
    }
}

crm.abc4rd.org{$default_site_port} {
    import proxy "abc4rd-erpnext-frontend:8080"
}
""".strip(),
    )
)
