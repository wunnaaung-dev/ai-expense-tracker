# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

FastAPI backend for an AI expense tracker. Users sign in with Google, connect their own Notion integration token, and log expenses from receipt images, free text, or voice recordings. Gemini extracts structured expenses; each user's Notion database is the system of record. Postgres stores only users, per-user storage config, and image-dedup hashes. Prompts handle Burmese, English and Thai input.

## Commands

Dependencies are managed with `uv` (Python >= 3.12, see `.python-version`).

```bash
uv sync                                   # install deps
uv run uvicorn main:app --reload          # run dev server (Swagger at /docs)
uv run alembic upgrade head               # apply migrations
uv run alembic revision --autogenerate -m "msg"   # new migration from models/
```

There is no test suite, linter, or formatter configured.

Deployment is Render (`render.yaml`): `uv sync --frozen`, then `uvicorn main:app --proxy-headers`. The env vars listed there predate the multi-user work (`NOTION_TOKEN`/`NOTION_DATABASE_ID` are no longer read).

## Configuration (`.env`)

- `core/config.py` (`Settings`): `GEMINI_API_KEY`, `REDIRECT_URI` (Google OAuth callback), `DATABASE_URL`, `JWT_SECRET`, `SESSION_SECRET`, `CREDENTIALS_ENCRYPTION_KEY` (a Fernet key), `COOKIE_SECURE`, `ALLOWED_ORIGINS` (comma-separated CORS list).
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` are read straight from `os.getenv` in `core/oauth.py`, not through `Settings`.
- `DATABASE_URL` is a plain `postgresql://` URL. `core/db.py` rewrites it to `postgresql+asyncpg://`, strips `sslmode`/`channel_binding`, and forces SSL with `statement_cache_size=0` (pgbouncer-safe). Alembic (`migrations/env.py`) uses the raw URL with sync psycopg2. Keep both paths working when you change the URL format.

## Architecture

Layering: `routers/` → `core/dependencies.py` (all DI wiring) → `services/` → `models/` (SQLAlchemy) and `schemas/` (Pydantic). `repositories/` is empty. Services are built per request through FastAPI `Depends` factories in `core/dependencies.py`. New services should be wired there, not built inside routers.

**Auth.** Google OAuth via Authlib (`/api/auth/google/login` → `/api/auth/google/callback`). Starlette `SessionMiddleware` only holds OAuth state. The callback upserts a `User` by `google_sub` and sets an HS256 JWT (7-day TTL) in an httpOnly `access_token` cookie (`core/cookies.py` holds the shared cookie kwargs). `core/security.get_current_user` reads that cookie. The `expense` and `storage` routers require it router-wide.

**Per-user storage.** `UserStorageConfig` holds one row per (user, provider), with the Notion token Fernet-encrypted (`core/crypto.py`) and `target_id` set to the Notion database ID. `PUT /api/v1/storage/notion` checks the token. If no usable database exists, it finds a page shared with the integration and creates the expense database there (`services/notion/notion_storage_service.py`). `get_storage_service` decrypts the token on every request and builds:

- `ResilientStorageService(primary=NotionStorageService, backup=BackupStorage)`: failed Notion inserts are appended to `data/backup/{user_id}.jsonl` (local disk) and replayed by `POST /api/v1/expense/sync-backup`.
- Everything implements `StorageStrategy` (`insert_many`, `get_all` with Notion cursor pagination). Notion property mapping is in `services/notion/property_config.py`.

**Extraction (strategy pattern).** `ImageExtractionStrategy`, `TextExtractionStrategy`, and `AudioExtractionStrategy` are ABCs. `GeminiExtractor` implements all three. Prompts and JSON parsing live in `services/extraction/gemini_extractor.py`. The model name is in `constants/ai_models.py` and the allowed categories (fallback `"General"`) are in `constants/categories.py`. The Gemini client calls are synchronous inside `async` methods.

**Orchestrators** (`services/upload/`) tie extraction, storage, and dedup together per input type:
- Images (`ExpenseUploadOrchestrator`): SHA-256 dedup against the request and the `expenses_dedup` table → concurrent extraction (`BatchExtractionProcessor`, semaphore of 3) → `insert_many` → mark hashes processed only for successful saves. Max 10 files per request.
- Text and voice: extract a list of expenses → `insert_many`. No dedup.
- Every endpoint returns per-item `BatchResult` (`status: success|failed`) and does not fail the whole request.

## Database / migrations

Models subclass `models.base.Base` and must be imported in `models/__init__.py` so Alembic autogenerate can see them. Datetime columns use `DateTime(timezone=True)` with UTC defaults. Uniqueness is enforced in the DB: `(user_id, provider)` on storage config and `(user_id, hash)` on dedup. `data/app.db` is a leftover SQLite file; the app uses Postgres.

API docs for the frontend are in `docs/API.md`, which is gitignored.
