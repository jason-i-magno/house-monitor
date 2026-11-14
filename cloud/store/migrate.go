package store

import (
	"context"
	"embed"
	"fmt"
	"io/fs"
	"sort"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Each file is a migration, applied once, in the order of their names: 001_…, 002_…. Never edit one that's been
// applied anywhere; add another.
//
//go:embed migrations/*.sql
var migrations embed.FS

// migrateLock is the advisory lock that lets one copy of the service migrate at a time, when two start at once.
const migrateLock = 0x686f7573656d6f6e // "housemon"

// Migrate brings the database's schema up to date: each migration not yet applied, each in a transaction of its
// own with its row in schema_migrations, so it's applied whole or not at all.
func Migrate(ctx context.Context, db *pgxpool.Pool) error {
	names, err := fs.Glob(migrations, "migrations/*.sql")
	if err != nil {
		return err
	}
	sort.Strings(names)
	for _, name := range names {
		sql, err := migrations.ReadFile(name)
		if err != nil {
			return err
		}
		if err := pgx.BeginFunc(ctx, db, func(tx pgx.Tx) error { return apply(ctx, tx, name, string(sql)) }); err != nil {
			return fmt.Errorf("migrating %s: %w", name, err)
		}
	}
	return nil
}

func apply(ctx context.Context, tx pgx.Tx, name, sql string) error {
	// Held until the transaction ends, so another copy of the service waits here, then finds it applied.
	if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", int64(migrateLock)); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, `CREATE TABLE IF NOT EXISTS schema_migrations (
		name text PRIMARY KEY,
		applied_at timestamptz NOT NULL DEFAULT now()
	)`)
	if err != nil {
		return err
	}
	tag, err := tx.Exec(ctx, "INSERT INTO schema_migrations (name) VALUES ($1) ON CONFLICT DO NOTHING", name)
	if err != nil || tag.RowsAffected() == 0 {
		return err // applied already, if no error
	}
	// With no arguments, pgx sends it as a simple query, so a file can hold many statements.
	_, err = tx.Exec(ctx, sql)
	return err
}
