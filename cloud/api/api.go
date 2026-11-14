// Package api is the cloud service's HTTP side: each house's page and the API it reads, as the Python server serves
// them (housemon/server.py), under /houses/{house}/, and the API a house's uplink writes its readings and layout to.
//
//	GET  /                                the default house, if there is one
//	GET  /houses/{house}/                 the page, and its app.js, model.js and style.css
//	GET  /houses/{house}/api/sensors      the house and each sensor's latest reading
//	GET  /houses/{house}/api/history      bucketed history, ?hours=24&simulated=0
//	GET  /houses/{house}/api/stream       new readings, as Server-Sent Events
//	POST /houses/{house}/api/readings     a batch of readings, with the house's token
//	PUT  /houses/{house}/api/house        the house as --dump prints it, with the house's token
//	GET  /healthz, /readyz                the process is up; and the database answers
//
// Only public houses can be read, for now: there's no signing in yet.
package api

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"math"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/jason-i-magno/house-monitor/cloud/house"
	"github.com/jason-i-magno/house-monitor/cloud/hub"
	"github.com/jason-i-magno/house-monitor/cloud/store"
)

const (
	MaxBatch        = 1000    // readings in one POST
	maxBatchBytes   = 1 << 20 // a full batch is about 150 KB
	maxLayoutBytes  = 4 << 20 // the example house is about 26 KB
	bodyTimeout     = 30 * time.Second
	MaxHistoryHours = 24 * 366
	HistoryPoints   = 500 // per sensor, so a chart stays light whatever the range
	// The shortest bucket: a house stores a reading per sensor a minute (--store-every), so a shorter one would only
	// split points.
	minBucket = 60 * time.Second
)

// A Server serves every house.
type Server struct {
	store        *store.Store
	hub          *hub.Hub
	assets       map[string]asset
	defaultHouse string
	log          *slog.Logger
	closing      chan struct{}
	closeOnce    sync.Once
}

// New serves the houses in s, sending each new reading on through h to the pages open on its house. page is the
// page's files (housemonitor.Web), and defaultHouse, if not empty, the house / leads to.
func New(s *store.Store, h *hub.Hub, page fs.FS, defaultHouse string, log *slog.Logger) (*Server, error) {
	assets, err := loadAssets(page)
	if err != nil {
		return nil, err
	}
	return &Server{store: s, hub: h, assets: assets, defaultHouse: defaultHouse, log: log, closing: make(chan struct{})}, nil
}

// Close ends every open stream, so a server shutting down isn't held open by pages, which reconnect, as to another
// copy of the service. Register it with http.Server.RegisterOnShutdown.
func (s *Server) Close() {
	s.closeOnce.Do(func() { close(s.closing) })
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /{$}", s.root)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, "ok\n") })
	mux.HandleFunc("GET /readyz", s.ready)
	mux.HandleFunc("GET /houses/{house}", s.withSlash)
	mux.HandleFunc("GET /houses/{house}/{$}", s.page)
	mux.HandleFunc("GET /houses/{house}/{file}", s.asset)
	mux.HandleFunc("GET /houses/{house}/api/sensors", s.sensors)
	mux.HandleFunc("GET /houses/{house}/api/history", s.history)
	mux.HandleFunc("GET /houses/{house}/api/stream", s.stream)
	mux.HandleFunc("POST /houses/{house}/api/readings", s.addReadings)
	mux.HandleFunc("PUT /houses/{house}/api/house", s.setLayout)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		mux.ServeHTTP(w, r)
	})
}

func (s *Server) root(w http.ResponseWriter, r *http.Request) {
	if s.defaultHouse == "" {
		http.NotFound(w, r)
		return
	}
	http.Redirect(w, r, "/houses/"+s.defaultHouse+"/", http.StatusFound)
}

func (s *Server) ready(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	if err := s.store.Ping(ctx); err != nil {
		http.Error(w, "the database isn't answering", http.StatusServiceUnavailable)
		return
	}
	io.WriteString(w, "ok\n")
}

// withSlash sends /houses/example on to /houses/example/, where the page's relative links resolve under the house.
func (s *Server) withSlash(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("house")
	if !house.ValidID(id) {
		http.NotFound(w, r)
		return
	}
	http.Redirect(w, r, "/houses/"+id+"/", http.StatusMovedPermanently)
}

// readable is the house a request reads, or false, with the reply sent, if there's no such house or it isn't
// public. A private house looks the same as none, so its id gives nothing away.
func (s *Server) readable(w http.ResponseWriter, r *http.Request) (string, bool) {
	id := r.PathValue("house")
	if !house.ValidID(id) {
		fail(w, http.StatusNotFound, "no such house")
		return "", false
	}
	h, err := s.store.House(r.Context(), id)
	if errors.Is(err, store.ErrNotFound) || err == nil && !h.Public {
		fail(w, http.StatusNotFound, "no such house")
		return "", false
	} else if err != nil {
		s.internal(w, r, err)
		return "", false
	}
	return id, true
}

// authorized is the house a request writes to, or false, with the reply sent, unless it carries that house's token.
func (s *Server) authorized(w http.ResponseWriter, r *http.Request) (string, bool) {
	id := r.PathValue("house")
	token, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	if !ok || token == "" {
		unauthorized(w, "send the house's token as Authorization: Bearer <token>")
		return "", false
	}
	err := s.store.Authorize(r.Context(), id, token)
	if errors.Is(err, store.ErrUnauthorized) {
		unauthorized(w, "not this house's token")
		return "", false
	} else if err != nil {
		s.internal(w, r, err)
		return "", false
	}
	// A body sent slowly enough would hold a request open as long as it liked.
	_ = http.NewResponseController(w).SetReadDeadline(time.Now().Add(bodyTimeout))
	return id, true
}

// A rejection is a reading of a batch that wasn't stored: its place in the batch and why.
type rejection struct {
	Index int    `json:"index"`
	Error string `json:"error"`
}

// addReadings stores a batch of readings from a house's uplink.
//
// The uplink sends a batch again until it hears back, so a batch may arrive more than once: readings already stored
// are counted as duplicates and not sent on again. A reading that can't be stored, as one with no temperature, is
// rejected and the rest of the batch stored: refusing the whole batch would have the uplink send it, and be refused,
// forever.
//
// It replies with how many were stored, how many were duplicates, and each one rejected and why.
func (s *Server) addReadings(w http.ResponseWriter, r *http.Request) {
	id, ok := s.authorized(w, r)
	if !ok {
		return
	}
	var batch struct {
		Readings []json.RawMessage `json:"readings"`
	}
	if !decodeBody(w, r, maxBatchBytes, &batch) {
		return
	}
	if len(batch.Readings) > MaxBatch {
		fail(w, http.StatusRequestEntityTooLarge, fmt.Sprintf("at most %d readings in a batch", MaxBatch))
		return
	}
	now := time.Now()
	valid := make([]house.Reading, 0, len(batch.Readings))
	rejected := []rejection{}
	for i, raw := range batch.Readings {
		reading, err := house.ParseReading(raw, now)
		if err != nil {
			rejected = append(rejected, rejection{i, err.Error()})
			continue
		}
		valid = append(valid, reading)
	}
	stored, err := s.store.AddReadings(r.Context(), id, valid)
	if err != nil {
		s.internal(w, r, err)
		return
	}
	s.hub.Publish(id, stored)
	duplicates := len(valid) - len(stored)
	s.log.Info("readings", "house", id, "stored", len(stored), "duplicates", duplicates, "rejected", len(rejected))
	if len(rejected) > 0 {
		s.log.Warn("rejected a reading", "house", id, "index", rejected[0].Index, "error", rejected[0].Error)
	}
	reply(w, map[string]any{"stored": len(stored), "duplicates": duplicates, "rejected": rejected})
}

// setLayout keeps the house as `python3 -m housemon --dump` prints it.
func (s *Server) setLayout(w http.ResponseWriter, r *http.Request) {
	id, ok := s.authorized(w, r)
	if !ok {
		return
	}
	layout, ok := readBody(w, r, maxLayoutBytes)
	if !ok {
		return
	}
	if _, _, err := parseLayout(layout); err != nil {
		fail(w, http.StatusBadRequest, err.Error())
		return
	}
	if err := s.store.SetLayout(r.Context(), id, layout); err != nil {
		s.internal(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// A house that hasn't sent its layout yet: no sensors, and nothing else to draw.
var emptyLayout = []byte(`{"sensors": [], "areas": [], "details": null, "litter_boxes": {}, "wall": null}`)

// parseLayout reads the house as --dump prints it, as much as the service needs to: its top level, and its sensors,
// each with its id. The rest goes to the page as it came.
func parseLayout(layout []byte) (map[string]json.RawMessage, []map[string]json.RawMessage, error) {
	var top map[string]json.RawMessage
	if err := json.Unmarshal(layout, &top); err != nil || top == nil {
		return nil, nil, errors.New("the house must be a JSON object, as python3 -m housemon --dump prints it")
	}
	var sensors []map[string]json.RawMessage
	if err := json.Unmarshal(top["sensors"], &sensors); err != nil || sensors == nil {
		return nil, nil, errors.New("the house must have a list of sensors")
	}
	for i, sensor := range sensors {
		var id string
		if err := json.Unmarshal(sensor["id"], &id); err != nil || id == "" {
			return nil, nil, fmt.Errorf("sensor %d has no id", i)
		}
	}
	return top, sensors, nil
}

// sensors is /api/sensors: the house as it sent it, with each sensor's latest reading in its place.
func (s *Server) sensors(w http.ResponseWriter, r *http.Request) {
	id, ok := s.readable(w, r)
	if !ok {
		return
	}
	layout, err := s.store.Layout(r.Context(), id)
	if err != nil {
		s.internal(w, r, err)
		return
	}
	if layout == nil {
		layout = emptyLayout
	}
	top, sensors, err := parseLayout(layout) // checked when it was sent, so this is only the database misbehaving
	if err != nil {
		s.internal(w, r, err)
		return
	}
	ids := make([]string, len(sensors))
	for i, sensor := range sensors {
		_ = json.Unmarshal(sensor["id"], &ids[i])
	}
	latest, err := s.store.Latest(r.Context(), id, ids)
	if err != nil {
		s.internal(w, r, err)
		return
	}
	for i, sensor := range sensors {
		sensor["reading"] = json.RawMessage("null")
		if reading, ok := latest[ids[i]]; ok {
			sensor["reading"] = mustMarshal(reading)
		}
	}
	top["sensors"] = mustMarshal(sensors)
	if _, ok := top["vacuums"]; !ok {
		top["vacuums"] = json.RawMessage("[]") // none yet: the uplink sends readings only
	}
	reply(w, top)
}

// history is /api/history?hours=24, with simulated=0 to leave out the simulator's readings, as the Python server
// gives it: each sensor's readings over the last `hours`, in buckets of whole minutes, at most HistoryPoints of them.
func (s *Server) history(w http.ResponseWriter, r *http.Request) {
	id, ok := s.readable(w, r)
	if !ok {
		return
	}
	query := r.URL.Query()
	hours := 24.0
	if query.Has("hours") {
		var err error
		if hours, err = strconv.ParseFloat(query.Get("hours"), 64); err != nil {
			hours = math.NaN()
		}
	}
	if !(0 < hours && hours <= MaxHistoryHours) {
		fail(w, http.StatusBadRequest, fmt.Sprintf("hours must be a number from 0 to %d", MaxHistoryHours))
		return
	}
	until := time.Now()
	since := until.Add(-time.Duration(hours * float64(time.Hour)))
	minutes := math.Ceil(hours * 3600 / HistoryPoints / 60) // as the Python server works it out, to the bit
	bucket := max(minBucket, time.Duration(minutes)*time.Minute)
	series, err := s.store.History(r.Context(), id, since, bucket, query.Get("simulated") != "0")
	if err != nil {
		s.internal(w, r, err)
		return
	}
	reply(w, map[string]any{
		"since": house.Seconds(since), "until": house.Seconds(until), "bucket": bucket.Seconds(), "series": series,
	})
}

// decodeBody reads a JSON body of at most limit bytes into v, or replies with why it can't and returns false.
func decodeBody(w http.ResponseWriter, r *http.Request, limit int64, v any) bool {
	body, ok := readBody(w, r, limit)
	if !ok {
		return false
	}
	if err := json.Unmarshal(body, v); err != nil {
		fail(w, http.StatusBadRequest, "not JSON as expected: "+err.Error())
		return false
	}
	return true
}

func readBody(w http.ResponseWriter, r *http.Request, limit int64) ([]byte, bool) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, limit))
	var tooBig *http.MaxBytesError
	if errors.As(err, &tooBig) {
		fail(w, http.StatusRequestEntityTooLarge, fmt.Sprintf("at most %d bytes", limit))
		return nil, false
	} else if err != nil {
		fail(w, http.StatusBadRequest, "couldn't read the body")
		return nil, false
	}
	return body, true
}

func mustMarshal(v any) json.RawMessage {
	data, err := json.Marshal(v)
	if err != nil {
		panic(err) // only values that always encode are passed
	}
	return data
}

func reply(w http.ResponseWriter, v any) {
	data := mustMarshal(v)
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.Write(data)
}

func fail(w http.ResponseWriter, code int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(code)
	w.Write(mustMarshal(map[string]string{"error": message}))
}

func unauthorized(w http.ResponseWriter, message string) {
	w.Header().Set("WWW-Authenticate", "Bearer")
	fail(w, http.StatusUnauthorized, message)
}

func (s *Server) internal(w http.ResponseWriter, r *http.Request, err error) {
	if r.Context().Err() == nil { // not just the client going away
		s.log.Error("request failed", "method", r.Method, "path", r.URL.Path, "error", err)
	}
	fail(w, http.StatusInternalServerError, "something went wrong on the server")
}

// An asset is one of the page's files, read once: they're built into the binary, so they can't change while it runs.
type asset struct {
	body        []byte
	contentType string
	etag        string
}

var assetTypes = map[string]string{
	"index.html": "text/html; charset=utf-8",
	"app.js":     "text/javascript; charset=utf-8",
	"model.js":   "text/javascript; charset=utf-8",
	"style.css":  "text/css; charset=utf-8",
}

func loadAssets(page fs.FS) (map[string]asset, error) {
	assets := make(map[string]asset, len(assetTypes))
	for name, contentType := range assetTypes {
		body, err := fs.ReadFile(page, name)
		if err != nil {
			return nil, err
		}
		sum := sha256.Sum256(body)
		assets[name] = asset{body, contentType, `"` + hex.EncodeToString(sum[:8]) + `"`}
	}
	return assets, nil
}

// page is the house's page. Its links are relative, so it reads the house it's served under.
func (s *Server) page(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.readable(w, r); ok {
		s.serveAsset(w, r, s.assets["index.html"])
	}
}

func (s *Server) asset(w http.ResponseWriter, r *http.Request) {
	a, ok := s.assets[r.PathValue("file")]
	if !ok || r.PathValue("file") == "index.html" {
		http.NotFound(w, r)
		return
	}
	s.serveAsset(w, r, a)
}

func (s *Server) serveAsset(w http.ResponseWriter, r *http.Request, a asset) {
	w.Header().Set("Content-Type", a.contentType)
	w.Header().Set("ETag", a.etag)
	w.Header().Set("Cache-Control", "no-cache") // kept, but checked each time, so a new version shows at once
	http.ServeContent(w, r, "", time.Time{}, bytes.NewReader(a.body))
}
