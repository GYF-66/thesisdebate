# PostgreSQL migrations

`001_format_revision.sql` defines the persistence contract for format profiles, versioned profile rules, format runs, and anchored format changes. It is idempotent and contains no credentials or connection strings.

The repository has a root-level `docker-compose.yml` that defines PostgreSQL, Redis, and MinIO services. The migration is not mounted into that compose service and is not applied automatically; the current API still keeps application state in memory. Apply this SQL explicitly with a PostgreSQL migration tool or client when setting up the database schema.
