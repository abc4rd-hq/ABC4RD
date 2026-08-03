# ABC4RD platform health monitor

The one-shot check verifies the required containers, public Academy endpoints,
the Keycloak redirect for both the portal and mobile guide, the internal mobile
guide marker, LiveKit ports, the reconciliation timer, recent participant state
and at least one generated pilot certificate. It also requires a runtime backup
newer than seven days and verifies the encrypted file against its SHA-256 sidecar.
All nine public ABC4RD service certificates are checked as served, and the monitor
fails before any of them reaches the final fourteen days of validity.
Host guardrails fail at 85% disk or inode use, below 15% available memory, any
failed systemd unit, or a pending reboot marker. These are availability alerts,
not an automatic cleanup or reboot mechanism.
The systemd timer runs every five minutes and writes one compact result to the
journal.

Install the script as `/usr/local/sbin/abc4rd-platform-health`, install both
units under `/etc/systemd/system`, then enable
`abc4rd-platform-health.timer`. A failing check exits non-zero and is visible
through `systemctl status` and `journalctl -u abc4rd-platform-health.service`.
