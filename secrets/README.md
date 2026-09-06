# secrets/

`secrets/empty/` is a committed placeholder directory only — the default bind-mount
source for `docker-compose.staging.yml`'s `api` service when `production` mode (and
therefore `trade_platform.secrets_manager.FileSecretProvider`) is not in use. It must
stay empty and tracked (via `.gitkeep`) so the compose file always has a valid mount
source, even when no real secrets directory has been provisioned yet.

Everything else under `secrets/` is gitignored (see `.gitignore`). For a `production`
deployment, set `TRADE_PLATFORM_SECRETS_DIR_HOST` to a real, POSIX-permission-locked
directory outside this repository containing one file per secret (at minimum
`CSRF_SIGNING_KEY` — see `docs/MODULE_3D_PRODUCTION_IDENTITY_SECRETS_AUDIT.md`).
Never create real secret files under this repository's `secrets/` directory itself.
