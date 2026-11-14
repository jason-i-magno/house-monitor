package api

import (
	"net/http"
	"time"
)

const (
	// KeepAlive is how long an idle stream goes before a comment is sent on it, so nothing in between, as a proxy,
	// closes it for being quiet.
	KeepAlive = 15 * time.Second
	// streamWriteTimeout is how long a write to a stream can take before the page is given up on.
	streamWriteTimeout = 10 * time.Second
)

// stream is /api/stream: the house's new readings as Server-Sent Events, each a plain message of its JSON, as the
// Python server sends them.
//
// It ends when the page goes, the page falls too far behind (hub.Client.Lagged), or the server shuts down (Close).
// The page reconnects on its own, then reloads the house and its history, so it fills in whatever it missed.
func (s *Server) stream(w http.ResponseWriter, r *http.Request) {
	id, ok := s.readable(w, r)
	if !ok {
		return
	}
	client := s.hub.Subscribe(id)
	defer s.hub.Unsubscribe(client)

	header := w.Header()
	header.Set("Content-Type", "text/event-stream")
	header.Set("Cache-Control", "no-cache")
	header.Set("X-Accel-Buffering", "no") // tells a proxy that buffers, as nginx does, to pass each event straight on
	w.WriteHeader(http.StatusOK)

	rc := http.NewResponseController(w)
	write := func(message []byte) bool {
		// Each write gets a deadline of its own, in place of the server's for the whole response, which a stream
		// open for hours would pass.
		_ = rc.SetWriteDeadline(time.Now().Add(streamWriteTimeout))
		if _, err := w.Write(message); err != nil {
			return false
		}
		return rc.Flush() == nil
	}
	if !write([]byte("retry: 3000\n\n")) { // the page reconnects 3 s after the stream drops
		return
	}
	keepalive := time.NewTicker(KeepAlive)
	defer keepalive.Stop()
	for {
		select {
		case message := <-client.Messages():
			if !write(message) {
				return
			}
			keepalive.Reset(KeepAlive)
		case <-keepalive.C:
			if !write([]byte(": keepalive\n\n")) {
				return
			}
		case <-client.Lagged():
			s.log.Info("dropped a page that fell behind", "house", id)
			return
		case <-s.closing:
			return
		case <-r.Context().Done():
			return
		}
	}
}
