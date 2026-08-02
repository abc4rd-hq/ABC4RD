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
""".strip(),
    )
)
