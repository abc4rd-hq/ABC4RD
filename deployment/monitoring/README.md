# ABC4RD platform health monitor

The one-shot check verifies the required containers, public Academy endpoints,
the reconciliation timer, recent participant state and at least one generated
pilot certificate. The systemd timer runs it every five minutes and writes one
compact result to the journal.

Install the script as `/usr/local/sbin/abc4rd-platform-health`, install both
units under `/etc/systemd/system`, then enable
`abc4rd-platform-health.timer`. A failing check exits non-zero and is visible
through `systemctl status` and `journalctl -u abc4rd-platform-health.service`.
