// Package housemonitor holds the page, so the cloud service (cloud/) serves the very files the Python server does.
//
// It lives at the module's root because go:embed can't reach up out of a package's own directory.
package housemonitor

import (
	"embed"
	"io/fs"
)

// The files the Python server serves, as its STATIC whitelist in housemon/server.py lists them.
//
//go:embed housemon/web/index.html housemon/web/app.js housemon/web/model.js housemon/web/garden.html housemon/web/garden.js housemon/web/plants.json housemon/web/style.css housemon/web/manual.html housemon/web/manual.js
var web embed.FS

// Web is the page: index.html, app.js, model.js and style.css; the garden planner's garden.html, garden.js and
// plants.json, which in the cloud keeps its plans in the browser; and the home manual's manual.html and manual.js,
// which in the cloud says the manual is kept at the house (its API is the house's own).
func Web() fs.FS {
	page, err := fs.Sub(web, "housemon/web")
	if err != nil {
		panic(err) // a constant path, so only a typo above
	}
	return page
}
