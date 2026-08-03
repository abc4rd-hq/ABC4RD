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


# Keep Open edX micro-frontends visually consistent with the dark ABC4RD portal.
# This is the compiled Indigo theme for the exact Open edX Ulmo release, pinned
# to an immutable upstream commit. Both preference variants deliberately use the
# dark bundle until ABC4RD ships its own light palette and theme switcher.
hooks.Filters.ENV_PATCHES.add_item(
    (
        "mfe-lms-common-settings",
        """
MFE_CONFIG["PARAGON_THEME_URLS"] = {
    "core": {
        "urls": {
            "default": "https://cdn.jsdelivr.net/npm/@openedx/paragon@$paragonVersion/dist/core.min.css",
            "brandOverride": "https://verify.abc4rd.org/static/openedx-theme.css",
        },
    },
    "defaults": {
        "light": "light",
        "dark": "dark",
    },
    "variants": {
        "light": {
            "urls": {
                "default": "https://verify.abc4rd.org/static/openedx-theme.css",
                "brandOverride": "https://verify.abc4rd.org/static/openedx-theme.css",
            },
        },
        "dark": {
            "urls": {
                "default": "https://verify.abc4rd.org/static/openedx-theme.css",
                "brandOverride": "https://verify.abc4rd.org/static/openedx-theme.css",
            },
        },
    },
}
""".strip(),
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
abc4rd.org{$default_site_port} {
    tls internal
    @matrix_server_well_known path /.well-known/matrix/server
    header @matrix_server_well_known Content-Type application/json
    respond @matrix_server_well_known `{"m.server":"matrix.abc4rd.org:443"}` 200
    respond 404
}

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

app.abc4rd.org{$default_site_port} {
    import proxy "abc4rd-portal-auth:4180"
}

verify.abc4rd.org{$default_site_port} {
    route {
        @public_verification path /health /c/* /static/openedx-theme.css
        reverse_proxy @public_verification "abc4rd-portal:8080" {
            header_up -X-Forwarded-Access-Token
            header_up -X-Forwarded-User
            header_up -X-Forwarded-Email
            header_up -X-Auth-Request-User
            header_up -X-Auth-Request-Email
        }
        respond 404
    }
}

library.abc4rd.org{$default_site_port} {
    redir https://app.abc4rd.org/library 302
}

chat.abc4rd.org{$default_site_port} {
    import proxy "abc4rd-element:80"
}

matrix.abc4rd.org{$default_site_port} {
    header {
        X-Content-Type-Options nosniff
        Referrer-Policy no-referrer
    }
    route {
        @matrix_client_well_known path /.well-known/matrix/client
        handle @matrix_client_well_known {
            header Content-Type application/json
            header Access-Control-Allow-Origin *
            respond `{"m.homeserver":{"base_url":"https://matrix.abc4rd.org"},"org.matrix.msc4143.rtc_foci":[{"type":"livekit","livekit_service_url":"https://matrix.abc4rd.org/livekit/jwt"}]}` 200
        }

        @matrix_rtc_auth path /livekit/jwt /livekit/jwt/*
        handle @matrix_rtc_auth {
            uri strip_prefix /livekit/jwt
            reverse_proxy "abc4rd-lk-jwt:8080" {
                header_up Host {host}
                header_up X-Forwarded-Server {host}
                header_up X-Real-IP {remote_host}
                header_up X-Forwarded-For {remote_host}
            }
        }

        @matrix_rtc_sfu path /livekit/sfu /livekit/sfu/*
        handle @matrix_rtc_sfu {
            uri strip_prefix /livekit/sfu
            reverse_proxy "abc4rd-livekit:7880" {
                header_up Host {host}
                header_up X-Forwarded-Server {host}
                header_up X-Real-IP {remote_host}
                header_up X-Forwarded-For {remote_host}
            }
        }

        handle {
            reverse_proxy "abc4rd-synapse:8008" {
                header_up X-Forwarded-Port 443
            }
        }
    }
}
""".strip(),
    )
)
