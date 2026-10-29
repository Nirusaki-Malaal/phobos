---
name: sql
description: Database discovery router — locate the DB service, ORM/migration stack, schema shape, and connect/migrate commands. Load on SQL/DATABASE_URL/migration/schema questions or DB-backed Python/Node work. Delegates performance, RLS, and indexing depth to supabase-postgres-best-practices.
---

# SQL (router)

> You own DB **discovery and safe access**. Performance/security depth lives upstream.
> Shared Hermes glue is canonical in the `agents` skill.

## Discover (never log raw `DATABASE_URL` — secret ref only)
1. **Service**: compose `image:`/`ports:`/`volumes:`/`POSTGRES_*`, version, named volume vs ephemeral (flag data-loss risk), `pg_isready` healthcheck.
2. **ORM/migrations**: Prisma (`schema.prisma`, `migrations/`) / Drizzle / Alembic (`alembic.ini`, `versions/`) / TypeORM / Sequelize / Knex; sample latest 2 migrations; record `migrate`/`db:push` commands.
3. **Schema**: tables, PKs/FKs, indexes, extensions (`pgvector`, `postgis`), naming + timestamp conventions; connection pooling in `src/`; raw SQL vs builder vs ORM (flag string-concat injection risk); seeds.
4. **Safety**: dry-run migrations first; `EXPLAIN ANALYZE` before optimization claims; destructive ops (`DROP`/`TRUNCATE`/`--force`) need explicit confirmation like any write.

## Delegate (load via `skill_view`)
| Need | Load |
|---|---|
| Query/index/RLS/pooling/locking/bloat depth | `supabase-postgres-best-practices` (`references/*.md`) |
| Container side of the DB | `docker` shim |
