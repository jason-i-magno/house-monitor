// Package storetest gives each test a Store on a real Postgres, in a schema of its own, so tests can run at once.
package storetest

import (
	"context"
	"crypto/rand"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jason-i-magno/house-monitor/cloud/store"
)

// URLVariable names the environment variable that points the tests at a database. They create and drop schemas in
// it, so give them one of their own.
const URLVariable = "HOUSECLOUD_TEST_DATABASE_URL"

// New is a Store in a new, migrated schema of its own, dropped when the test ends. The test is skipped if
// URLVariable isn't set.
func New(t *testing.T) *store.Store {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	s, err := store.OpenConfig(ctx, Config(t))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Close) // before the schema's dropped: cleanups run last first
	return s
}

// Config is a pool's config for a new, empty schema of its own, dropped when the test ends, as to open stores on it
// with store.OpenConfig. The test is skipped if URLVariable isn't set.
func Config(t *testing.T) *pgxpool.Config {
	t.Helper()
	url := os.Getenv(URLVariable)
	if url == "" {
		t.Skipf("set %s to run the tests that need Postgres", URLVariable)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	schema := "test_" + strings.ToLower(rand.Text())
	admin, err := pgx.Connect(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close(ctx)
	if _, err := admin.Exec(ctx, "CREATE SCHEMA "+schema); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		conn, err := pgx.Connect(ctx, url)
		if err != nil {
			t.Error(err)
			return
		}
		defer conn.Close(ctx)
		if _, err := conn.Exec(ctx, "DROP SCHEMA "+schema+" CASCADE"); err != nil {
			t.Error(err)
		}
	})

	config, err := pgxpool.ParseConfig(url)
	if err != nil {
		t.Fatal(err)
	}
	config.ConnConfig.RuntimeParams["search_path"] = schema
	return config
}
