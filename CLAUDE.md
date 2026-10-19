# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

FastAPI backend for an AI expense tracker. Users sign in with Google or Notion and log expenses from receipt images, free text, or voice recordings. Gemini extracts structured expenses; each user's own Google Sheet or Notion database is the system of record (the default comes from the login provider). Postgres stores only users, per-user storage config (encrypted credentials, target ID, status), and image-dedup hashes; the server never stores financial records. Prompts handle Burmese, English and Thai input.

## Commands

Dependencies are managed with `uv` (Python >= 3.12, see `.python-version`).

```bash
uv sync                                   # install deps (incl. dev group: pytest)
uv run uvicorn main:app --reload          # run dev server (Swagger at /docs)
uv run pytest -q                          # tests (SQLite in memory + FakeGoogle; no network)
uv run pytest tests/test_sheets_storage.py::test_columns_reordered   # single test
uv run alembic upgrade head               # apply migrations
uv run alembic revision --autogenerate -m "msg"   # new migration from models/
```

`tests/conftest.py` sets test env vars before any app import, so tests never use the real `.env` secrets or database. `tests/test_provisioning_concurrency.py` needs real Postgres: it runs only when `TEST_DATABASE_URL` is set (it refuses to run against `DATABASE_URL` and uses a throwaway schema). No linter or formatter is configured.

Deployment is Render (`render.yaml`): `uv sync --frozen`, then `uvicorn main:app --proxy-headers`. The env vars listed there predate the multi-user work (`NOTION_TOKEN`/`NOTION_DATABASE_ID` are no longer read).

## Configuration (`.env`)

- `core/config.py` (`Settings`): `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (**required**: startup fails if missing or empty), `GEMINI_API_KEY`, `REDIRECT_URI` (Google OAuth callback), `NOTION_CLIENT_ID` / `NOTION_CLIENT_SECRET` / `NOTION_REDIRECT_URI`, `FRONTEND_URL` (both login callbacks redirect to `{FRONTEND_URL}/auth/callback`), `DATABASE_URL`, `JWT_SECRET`, `SESSION_SECRET`, `CREDENTIALS_ENCRYPTION_KEY` (a Fernet key), `COOKIE_SECURE`, `ALLOWED_ORIGINS` (comma-separated CORS list).
- `DATABASE_URL` is a plain `postgresql://` URL. `core/db.py` rewrites it to `postgresql+asyncpg://`, strips `sslmode`/`channel_binding`, and forces SSL with `statement_cache_size=0` (pgbouncer-safe). Alembic (`migrations/env.py`) uses the raw URL with sync psycopg2. Keep both paths working when you change the URL format.

## Architecture

Layering: `routers/` → `core/dependencies.py` (all DI wiring) → `services/` → `models/` (SQLAlchemy) and `schemas/` (Pydantic). `repositories/` is empty. Services are built per request through FastAPI `Depends` factories in `core/dependencies.py`. New services should be wired there, not built inside routers. Storage domain errors are mapped to typed JSON bodies (`{"code": ...}`) in `core/errors.py`.

**Auth.** Authlib OAuth for Google (`/api/auth/google/login` → `/callback`) and Notion (`/api/auth/notion/login` → `/callback`). Starlette `SessionMiddleware` holds OAuth state (and a one-shot Google re-consent flag). Both callbacks set an HS256 JWT (7-day TTL) in an httpOnly `access_token` cookie (`core/cookies.py`) and redirect to the frontend; `core/security.get_current_user` reads that cookie. The `expense` and `storage` routers require it router-wide.

Google requests `openid email profile drive.file` with `access_type=offline` (`core/oauth.py`). `drive.file` (non-sensitive) covers every Sheets/Drive call on files the app created; don't add the sensitive `spreadsheets` scope. `AuthService.login_with_google` upserts the user, stores credentials via `services/google/credentials.py` (keeps the old refresh token when Google omits one), then provisions the spreadsheet; provisioning failures never fail the login. `get_google_access_token` refreshes expired access tokens on use.

**Per-user storage.** `UserStorageConfig` holds one row per (user, provider): Fernet-encrypted credentials (`core/crypto.py`), `target_id` (Notion database ID or spreadsheet ID), and `storage_status` (`none | provisioning | ready | failed | needs_permission | schema_broken | missing`, constants in `constants/storage_status.py`). `get_storage_service` routes by `user.default_storage_provider` and raises `StorageNotReadyError` (409 `storage_not_ready`) unless that row is `ready`. Everything implements `StorageStrategy` (`insert_many`, `get_all`). There is no server-side backup: a failed write raises `StorageWriteError` (502 `storage_write_failed`) and the client retries.

- **Notion**: `PUT /api/v1/storage/notion` checks a pasted integration token and finds or creates the expense database (`services/notion/`). Property mapping by display name is in `services/notion/property_config.py`.
- **Google Sheets** (`services/google_sheets/`, Google calls in `services/google/google_api.py`, an async httpx client):
  - `provisioning.py`: verify the stored file via Drive (Sheets still serves trashed files) → claim with a compare-and-set on `storage_status` (2-minute lease, so concurrent logins create one file) → reuse a file tagged with appProperty `app=ai-expense-tracker` → else create it. `POST /api/v1/storage/provision` re-runs this.
  - `schema.py` is the single source of the column layout (`FIELDS`: required `id/date/type/amount`, optional `category/description/payment_channel/source`) and the setup requests.
  - `google_sheets_storage.py`: columns are located **only** via Developer Metadata (`et_sheet` on the tab, `et_field` on each column), never by header text, column letter, tab title or row number; all Sheets calls use `sheetId`/gridRange. Reads use a per-user `TTLCache` of the mapping; writes always re-resolve. Records are addressed by the UUID in the `id` column. `repair()` (`POST /api/v1/storage/repair`) re-adds missing required columns or the tab.

**Extraction (strategy pattern).** `ImageExtractionStrategy`, `TextExtractionStrategy`, and `AudioExtractionStrategy` are ABCs. `GeminiExtractor` implements all three. Prompts and JSON parsing live in `services/extraction/gemini_extractor.py`. The model name is in `constants/ai_models.py` and the allowed categories (fallback `"General"`) are in `constants/categories.py`. The Gemini client calls are synchronous inside `async` methods. `ExpenseData` also has optional `id`, `type` (default `expense`), `payment_channel` and `source`; the prompts don't produce them yet.

**Orchestrators** (`services/upload/`) tie extraction, storage, and dedup together per input type and tag each item's `source` (`slip`/`text`/`voice`):
- Images (`ExpenseUploadOrchestrator`): SHA-256 dedup against the request and the `expenses_dedup` table → concurrent extraction (`BatchExtractionProcessor`, semaphore of 3) → `insert_many` → mark hashes processed only for successful saves. Max 10 files per request.
- Text and voice: extract a list of expenses → `insert_many`. No dedup.
- Endpoints return per-item `BatchResult` (`status: success|failed`); a storage-level failure raises a typed error instead.

## Database / migrations

Models subclass `models.base.Base` and must be imported in `models/__init__.py` so Alembic autogenerate can see them. Datetime columns use `DateTime(timezone=True)` with UTC defaults. Uniqueness is enforced in the DB: `(user_id, provider)` on storage config and `(user_id, hash)` on dedup; concurrent first logins rely on it (savepoint insert + `IntegrityError` re-select). `data/app.db` is a leftover SQLite file; the app uses Postgres.

API docs for the frontend are in `docs/API.md` and `docs/AUTH.md`, which are gitignored.
