# Novels Paradise: Production Architecture Execution Report

This document reports on the successful transition of the backend architecture of Novels Paradise from a local SQLite prototype to a scalable production-grade design supporting PostgreSQL, Redis, Cloudflare R2, and rate-limiting.

## 1. Database Portability & Driver Abstraction
A custom connection and cursor wrapper system has been implemented to handle both **SQLite** and **PostgreSQL (via pg8000)** transparently. It maps SQL dialect variations automatically:

- **Placeholders**: Translates SQLite `?` to PostgreSQL `%s`.
- **Row Mapping**: Introduces a `PgRow` dictionary wrapper to support positional index checks (e.g. `row[0]`) to match `sqlite3.Row` functionality.
- **UPSERT / ON CONFLICT**: Rewrites SQLite `INSERT OR IGNORE` and `INSERT OR REPLACE` into standard PostgreSQL `ON CONFLICT` clauses.
- **Schema Reflection**: Translates SQLite `PRAGMA table_info` queries to query `information_schema.columns`.

This allows the backend to work out of the box with PostgreSQL simply by configuring `LIGHTNOVEL_DATABASE_URL` in the environment.

## 2. Redis Caching & Buffered View Counting
To eliminate high-concurrency database locking:
- **Redis Cache Layer**: The application checks for `LIGHTNOVEL_REDIS_URL` and initializes a `RedisCache` module. If not set, it falls back gracefully to a memory-based `MockCache` for development/testing.
- **Page Caching**: Caching is enabled for the public endpoints:
  - Homepage latest novels (`homepage:latest`) with 1 minute TTL.
  - Novel detail response (`novel:detail:<id>`) with 5 minutes TTL (bypassed for staff to ensure real-time previewing).
  - Public chapters (`chapter:public:<id>`) with 1 hour TTL (cached only if free and published).
- **Buffered View Counting**: The database update query `UPDATE novels SET views = views + 1` has been replaced with a Redis-buffered counter (`cache.incr`). A daemon background thread (`flush_views_to_db`) collects all view updates and flushes them to the database in batch every 60 seconds, eliminating synchronous database write bottlenecks.

## 3. Rate-Limiting & Security Headers
- **Brute-Force Protection**: Integrated `Flask-Limiter` with default limits of `2000 per hour` and `100 per minute`. Enforced strict limits:
  - `/api/auth/register`: `3 per minute`
  - `/api/auth/login`: `5 per minute`
  - `/api/subscribe`: `5 per minute`
- **Security Headers**: Injected custom middleware headers:
  - `X-Frame-Options: DENY`
  - `X-Content-Type-Options: nosniff`
  - `X-XSS-Protection: 1; mode=block`
  - Strict Content-Security-Policy (CSP) that restricts iframe embedding and allowed resource domains.

## 4. Media & CDN Uploads
Implemented a unified `/api/admin/upload` endpoint allowing staff users to upload novel covers and illustrations:
- **Format Optimization**: Automatically converts uploaded images to optimized **WebP** format using the Pillow library.
- **Cloudflare R2 Integration**: If `LIGHTNOVEL_R2_BUCKET` is configured, files are uploaded directly to the cloud storage bucket using `boto3`.
- **Local Fallback**: If R2 is not configured, files are saved locally to a folder and served statically by a new Flask endpoint.

## 5. Verification & Testing
Added a new test case `test_production_architecture_scaling_phase4` in the test suite to verify:
- **Paginated Chapters**: Verified that `/api/novels/<novel_id>/chapters` works and supports pagination.
- **Views Cache & Flushing**: Verified that view increments are stored in the cache and only written to the database when the flusher is triggered.

All 8 tests pass successfully.
