// Command housecloud is house-monitor's cloud service: it takes readings from houses' uplinks, stores them in
// Postgres, and serves each house's page, live.
//
//	housecloud serve [-listen :8080] [-default-house example]
//	housecloud house add -id example -name "Example house" [-public]
//	housecloud house token -id example
//
// Each connects to the database DATABASE_URL names, and brings its schema up to date first.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	housemonitor "github.com/jason-i-magno/house-monitor"
	"github.com/jason-i-magno/house-monitor/cloud/api"
	"github.com/jason-i-magno/house-monitor/cloud/hub"
	"github.com/jason-i-magno/house-monitor/cloud/store"
)

const usage = `usage:
  housecloud serve [-listen :8080] [-default-house id]   serve the houses
  housecloud house add -id id -name name [-public]       add a house, and print its ingest token
  housecloud house token -id id                          replace a house's ingest token, and print it

The database is the one DATABASE_URL names, as postgres://user:password@host:5432/database.
`

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stderr, nil))
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx, os.Args[1:], log); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			os.Exit(2)
		}
		fmt.Fprintln(os.Stderr, "housecloud:", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, args []string, log *slog.Logger) error {
	switch {
	case len(args) >= 1 && args[0] == "serve":
		return serve(ctx, args[1:], log)
	case len(args) >= 2 && args[0] == "house" && args[1] == "add":
		return addHouse(ctx, args[2:])
	case len(args) >= 2 && args[0] == "house" && args[1] == "token":
		return newToken(ctx, args[2:])
	}
	fmt.Fprint(os.Stderr, usage)
	return flag.ErrHelp
}

func open(ctx context.Context) (*store.Store, error) {
	url := os.Getenv("DATABASE_URL")
	if url == "" {
		return nil, errors.New("set DATABASE_URL to the database, as postgres://user:password@host:5432/database")
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	return store.Open(ctx, url)
}

func serve(ctx context.Context, args []string, log *slog.Logger) error {
	flags := flag.NewFlagSet("serve", flag.ContinueOnError)
	listen := flags.String("listen", ":8080", "address to serve on")
	defaultHouse := flags.String("default-house", "", "the house / leads to, if any")
	if err := flags.Parse(args); err != nil {
		return err
	}
	db, err := open(ctx)
	if err != nil {
		return err
	}
	defer db.Close()
	houses, err := api.New(db, hub.New(), housemonitor.Web(), *defaultHouse, log)
	if err != nil {
		return err
	}
	server := &http.Server{
		Addr:    *listen,
		Handler: houses.Handler(),
		// No ReadTimeout: it would cut off streams, which stay open for hours. The handlers that read a body set a
		// deadline of their own, and streams give each write one.
		ReadHeaderTimeout: 10 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       2 * time.Minute,
		ErrorLog:          slog.NewLogLogger(log.Handler(), slog.LevelWarn),
	}
	server.RegisterOnShutdown(houses.Close) // or open streams would hold Shutdown up until it gave up

	failed := make(chan error, 1)
	go func() { failed <- server.ListenAndServe() }()
	log.Info("serving", "address", *listen, "default_house", *defaultHouse)
	select {
	case err := <-failed:
		return err
	case <-ctx.Done():
	}
	log.Info("shutting down")
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	return server.Shutdown(ctx)
}

func addHouse(ctx context.Context, args []string) error {
	flags := flag.NewFlagSet("house add", flag.ContinueOnError)
	id := flags.String("id", "", "the house's id, as in its URL: lower-case letters, digits and hyphens")
	name := flags.String("name", "", "the house's name")
	public := flags.Bool("public", false, "let anyone read it, without signing in; only for a simulated house")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *id == "" || *name == "" {
		return errors.New("a house needs an -id and a -name")
	}
	db, err := open(ctx)
	if err != nil {
		return err
	}
	defer db.Close()
	token, err := db.AddHouse(ctx, store.House{ID: *id, Name: *name, Public: *public})
	if err != nil {
		return err
	}
	printToken(*id, token)
	return nil
}

func newToken(ctx context.Context, args []string) error {
	flags := flag.NewFlagSet("house token", flag.ContinueOnError)
	id := flags.String("id", "", "the house's id")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *id == "" {
		return errors.New("which house? give its -id")
	}
	db, err := open(ctx)
	if err != nil {
		return err
	}
	defer db.Close()
	token, err := db.NewToken(ctx, *id)
	if err != nil {
		return err
	}
	printToken(*id, token)
	return nil
}

func printToken(id, token string) {
	fmt.Fprintf(os.Stderr, "The ingest token for %s. Keep it secret: it isn't kept here, and can't be shown again.\n", id)
	fmt.Println(token) // alone on stdout, so a script can take it
}
