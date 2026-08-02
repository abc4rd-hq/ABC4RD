# ABC4RD runtime backup

`create-runtime-backup.sh` creates an encrypted recovery bundle for the runtime
components added around Open edX:

- Keycloak PostgreSQL;
- Matrix/Synapse PostgreSQL, media and the homeserver signing key;
- Academy Core SQLite;
- the learner-portal projection and protected runtime configuration.

Open edX and ERPNext are deliberately excluded because they have separate
logical backup and restore procedures. Combining all databases into a single
filesystem snapshot would incorrectly imply transactional consistency across
independent systems.

The script accepts the password only on stdin, creates plaintext only inside a
root-owned temporary directory, verifies both PostgreSQL custom dumps and the
SQLite backup before encryption, then decrypts the resulting GPG stream into
`tar -t` as a final archive check. The temporary plaintext is removed on exit.
Filesystem snapshots may still retain deleted blocks, so the server disk itself
must remain access-controlled and encrypted infrastructure is preferred.

Run on the server as root:

```bash
printf '%s\n' "$ABC4RD_BACKUP_PASSWORD" | sudo ./create-runtime-backup.sh
```

The master password is stored in 1Password vault `ABC4RD`, item
`ABC4RD BACKUP ENCRYPTION`. A backup is not off-host until the `.gpg` file and
its `.sha256` file are copied elsewhere and the checksum matches.
