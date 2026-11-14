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
//go:embed housemon/web/index.html housemon/web/app.js housemon/web/model.js housemon/web/style.css
var web embed.FS

// Web is the page: index.html, app.js, model.js and style.css.
func Web() fs.FS {
	page, err := fs.Sub(web, "housemon/web")
	if err != nil {
		panic(err) // a constant path, so only a typo above
	}
	return page
}
