# dsh-pai-telegram

First-party Telegram access to DSH (host-only, no client bundle).

Phase 3 scaffold: long-poll `getUpdates` + allowlist gate + chat→session
binding (with `cwd`) + `/workspace` + `/status` + `/help`. Agent driving,
live drafts, approvals/questions land in Phase 4.

- Token: `DSH_TELEGRAM_BOT_TOKEN` (fallback `DSH_TELEGRAM_TOKEN`, legacy
  `TELEGRAM_BOT_TOKEN`). Never logged.
- Allowlist: `DSH_TELEGRAM_ALLOWED_USERS` (numeric chat IDs, empty = deny-all,
  silent drop). `DSH_TELEGRAM_GROUP_*` ignored in v1 (DM-only).
- State: `$DSH_HOME/storages/telegram/{state,bindings}.json` (offset + cwd).

```bash
node --check lib/index.js && node --check lib/util.js && node --test test/*.test.js
```

`node --test test/` (bare directory) no longer works on Node 24 — it resolves
`test/` as a module path and fails with MODULE_NOT_FOUND. Pass the files.

- Group support is live (README history said "DM-only in v1" — that is stale).
  Admission is `groupAdmission()` in `lib/util.js`: owners may use any chat,
  members only allowlisted chats. A bot sender must be explicitly allowlisted.
- Bot-to-bot loop guard: `botStreakTrips` caps 5 consecutive bot-triggered turns
  per chat; any human message resets it.
