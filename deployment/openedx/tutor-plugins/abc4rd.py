"""ABC4RD production settings for Tutor/Open edX."""

from tutor import hooks


hooks.Filters.ENV_PATCHES.add_item(
    (
        "openedx-lms-common-settings",
        "FEATURES['ALLOW_PUBLIC_ACCOUNT_CREATION'] = False",
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
