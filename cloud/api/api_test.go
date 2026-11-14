package api_test

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	housemonitor "github.com/jason-i-magno/house-monitor"
	"github.com/jason-i-magno/house-monitor/cloud/api"
	"github.com/jason-i-magno/house-monitor/cloud/hub"
	"github.com/jason-i-magno/house-monitor/cloud/store"
	"github.com/jason-i-magno/house-monitor/cloud/store/storetest"
)

// A fixture is the service on a test server, with two houses: example, public, and mine, private.
type fixture struct {
	t      *testing.T
	api    *api.Server
	url    string
	tokens map[string]string
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	s := storetest.New(t)
	a, err := api.New(s, hub.New(), housemonitor.Web(), "example", slog.New(slog.DiscardHandler))
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(a.Handler())
	t.Cleanup(server.Close)
	t.Cleanup(a.Close) // first, so open streams end and Close isn't left waiting on them
	f := &fixture{t, a, server.URL, map[string]string{}}
	for _, h := range []store.House{{ID: "example", Name: "Example", Public: true}, {ID: "mine", Name: "Mine"}} {
		token, err := s.AddHouse(context.Background(), h)
		if err != nil {
			t.Fatal(err)
		}
		f.tokens[h.ID] = token
	}
	return f
}

// do sends a request, with the token if it's given, and returns the response with its body read.
func (f *fixture) do(method, path, token string, body string) (*http.Response, []byte) {
	f.t.Helper()
	req, err := http.NewRequest(method, f.url+path, strings.NewReader(body))
	if err != nil {
		f.t.Fatal(err)
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	client := http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	resp, err := client.Do(req)
	if err != nil {
		f.t.Fatal(err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		f.t.Fatal(err)
	}
	return resp, data
}

func (f *fixture) get(path string) (*http.Response, []byte) {
	f.t.Helper()
	return f.do("GET", path, "", "")
}

// send posts readings to a house with its own token, and returns what it said.
func (f *fixture) send(id string, readings ...string) ingestReply {
	f.t.Helper()
	resp, body := f.do("POST", "/houses/"+id+"/api/readings", f.tokens[id], `{"readings": [`+strings.Join(readings, ",")+`]}`)
	if resp.StatusCode != http.StatusOK {
		f.t.Fatalf("POST readings: %s %s", resp.Status, body)
	}
	var reply ingestReply
	if err := json.Unmarshal(body, &reply); err != nil {
		f.t.Fatal(err)
	}
	return reply
}

type ingestReply struct {
	Stored, Duplicates int
	Rejected           []struct {
		Index int
		Error string
	}
}

func reading(sensor string, ts float64, temp float64) string {
	return fmt.Sprintf(`{"sensor_id": %q, "ts": %v, "temperature_c": %v, "humidity": 40.5, "battery": 90, "rssi": -60,
		"simulated": true}`, sensor, ts, temp)
}

func now() float64 { return float64(time.Now().UnixMicro()) / 1e6 }

func TestServesThePageUnderEachHouse(t *testing.T) {
	f := newFixture(t)
	resp, body := f.get("/houses/example/")
	if resp.StatusCode != 200 || !strings.HasPrefix(resp.Header.Get("Content-Type"), "text/html") {
		t.Fatalf("page: %s %s", resp.Status, resp.Header.Get("Content-Type"))
	}
	// Its links are relative, so they resolve under the house.
	if !bytes.Contains(body, []byte(`src="app.js"`)) || bytes.Contains(body, []byte(`src="/`)) {
		t.Errorf("the page's links aren't relative:\n%s", body)
	}
	for file, kind := range map[string]string{"app.js": "text/javascript", "model.js": "text/javascript", "style.css": "text/css"} {
		resp, body := f.get("/houses/example/" + file)
		if resp.StatusCode != 200 || !strings.HasPrefix(resp.Header.Get("Content-Type"), kind) || len(body) < 100 {
			t.Errorf("%s: %s %s, %d bytes", file, resp.Status, resp.Header.Get("Content-Type"), len(body))
		}
	}

	// Asked again with its ETag, a file is still fresh.
	resp, _ = f.get("/houses/example/app.js")
	req, _ := http.NewRequest("GET", f.url+"/houses/example/app.js", nil)
	req.Header.Set("If-None-Match", resp.Header.Get("ETag"))
	again, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	again.Body.Close()
	if again.StatusCode != http.StatusNotModified {
		t.Errorf("with its ETag: %s", again.Status)
	}

	for path, to := range map[string]string{"/": "/houses/example/", "/houses/example": "/houses/example/"} {
		resp, _ := f.get(path)
		if resp.StatusCode/100 != 3 || resp.Header.Get("Location") != to {
			t.Errorf("%s: %s to %q, want to %q", path, resp.Status, resp.Header.Get("Location"), to)
		}
	}
	for _, path := range []string{"/houses/example/server.py", "/houses/example/index.html", "/houses/example/api/nothing", "/style.css"} {
		if resp, _ := f.get(path); resp.StatusCode != 404 {
			t.Errorf("%s: %s", path, resp.Status)
		}
	}
}

func TestPrivateHousesCantBeRead(t *testing.T) {
	f := newFixture(t)
	f.send("mine", reading("office", now(), 20))
	for _, id := range []string{"mine", "nowhere", "Not-An-ID"} {
		for _, path := range []string{"/", "/api/sensors", "/api/history", "/api/stream"} {
			if resp, body := f.get("/houses/" + id + path); resp.StatusCode != 404 {
				t.Errorf("%s%s: %s %s", id, path, resp.Status, body)
			}
		}
	}
}

func TestWritingTakesTheHousesToken(t *testing.T) {
	f := newFixture(t)
	batch := `{"readings": [` + reading("office", now(), 20) + `]}`
	for _, try := range []struct{ house, token string }{
		{"example", ""},
		{"example", f.tokens["mine"]},
		{"example", "hm_wrong"},
		{"nowhere", f.tokens["example"]},
	} {
		for _, req := range []struct{ method, path, body string }{
			{"POST", "/api/readings", batch},
			{"PUT", "/api/house", `{"sensors": []}`},
		} {
			resp, body := f.do(req.method, "/houses/"+try.house+req.path, try.token, req.body)
			if resp.StatusCode != http.StatusUnauthorized || resp.Header.Get("WWW-Authenticate") != "Bearer" {
				t.Errorf("%s %s with %q: %s %s", req.method, req.path, try.token, resp.Status, body)
			}
		}
	}
	// And nothing got in.
	_, body := f.get("/houses/example/api/history")
	if !bytes.Contains(body, []byte(`"series":{}`)) {
		t.Errorf("history after refused writes: %s", body)
	}
}

func TestABatchSentAgainIsStoredOnce(t *testing.T) {
	f := newFixture(t)
	t0 := now()
	batch := []string{
		reading("office", t0-60, 20),
		`{"sensor_id": "office", "ts": 1800000000}`, // no temperature
		reading("kitchen", t0-60, 21),
	}
	reply := f.send("example", batch...)
	if reply.Stored != 2 || reply.Duplicates != 0 || len(reply.Rejected) != 1 || reply.Rejected[0].Index != 1 {
		t.Errorf("first time: %+v", reply)
	}
	reply = f.send("example", batch...) // as when the uplink didn't hear back
	if reply.Stored != 0 || reply.Duplicates != 2 || len(reply.Rejected) != 1 {
		t.Errorf("sent again: %+v", reply)
	}
	if reply := f.send("example"); reply.Stored != 0 || reply.Duplicates != 0 || reply.Rejected == nil {
		t.Errorf("empty batch: %+v", reply) // rejected is [] rather than null, for the uplink to range over
	}
}

func TestBatchesThatCantBeRead(t *testing.T) {
	f := newFixture(t)
	token := f.tokens["example"]
	big := make([]string, api.MaxBatch+1)
	for i := range big {
		big[i] = reading("office", now()-float64(i), 20)
	}
	for _, try := range []struct {
		body string
		want int
	}{
		{"not json", http.StatusBadRequest},
		{`[` + reading("office", now(), 20) + `]`, http.StatusBadRequest}, // readings go in an object
		{`{"readings": [` + strings.Join(big, ",") + `]}`, http.StatusRequestEntityTooLarge},
		{`{"readings": "` + strings.Repeat("x", 2<<20) + `"}`, http.StatusRequestEntityTooLarge},
	} {
		resp, body := f.do("POST", "/houses/example/api/readings", token, try.body)
		if resp.StatusCode != try.want || !bytes.Contains(body, []byte(`"error"`)) {
			t.Errorf("%.40s…: %s %s, want %d", try.body, resp.Status, body, try.want)
		}
	}
}

// A house as --dump prints it, cut down: two sensors, an area, and a litter box with no state yet.
const layout = `{
	"sensors": [
		{"id": "office", "name": "Office", "floor": "Main", "plan": null, "heated": true, "outside": false, "reading": null},
		{"id": "garage", "name": "Garage", "floor": "Main", "plan": null, "heated": false, "outside": false, "reading": null}
	],
	"areas": [{"name": "Hall", "floor": "Main", "plan": {"x": 0, "y": 0, "width": 4, "depth": 10}}],
	"details": null,
	"litter_boxes": {"Litter box": null},
	"wall": 0.5
}`

func TestSensorsAreTheHouseWithItsLatestReadings(t *testing.T) {
	f := newFixture(t)
	var house struct {
		Sensors []struct {
			ID      string
			Heated  bool
			Reading *struct {
				TS           float64
				TemperatureC float64 `json:"temperature_c"`
			}
		}
		Areas       []map[string]any
		LitterBoxes map[string]any `json:"litter_boxes"`
		Wall        *float64
		Vacuums     []any
	}
	_, body := f.get("/houses/example/api/sensors")
	if err := json.Unmarshal(body, &house); err != nil || len(house.Sensors) != 0 || house.Vacuums == nil {
		t.Errorf("before the house sent its layout: %s, %v", body, err)
	}

	if resp, body := f.do("PUT", "/houses/example/api/house", f.tokens["example"], layout); resp.StatusCode != 204 {
		t.Fatalf("PUT house: %s %s", resp.Status, body)
	}
	t0 := now()
	f.send("example", reading("office", t0-120, 20), reading("office", t0-60, 20.5), reading("elsewhere", t0, 9))
	_, body = f.get("/houses/example/api/sensors")
	if err := json.Unmarshal(body, &house); err != nil {
		t.Fatal(err)
	}
	if len(house.Sensors) != 2 || house.Sensors[0].ID != "office" || house.Sensors[1].ID != "garage" {
		t.Fatalf("sensors, in the house's order: %s", body)
	}
	if r := house.Sensors[0].Reading; r == nil || r.TemperatureC != 20.5 {
		t.Errorf("office's latest reading: %+v", r)
	}
	if house.Sensors[1].Reading != nil || house.Sensors[1].Heated {
		t.Errorf("garage: %+v", house.Sensors[1])
	}
	if len(house.Areas) != 1 || house.Wall == nil || *house.Wall != 0.5 || len(house.LitterBoxes) != 1 || house.Vacuums == nil {
		t.Errorf("the rest of the house: %s", body)
	}

	for _, bad := range []string{`[]`, `{}`, `{"sensors": [{"name": "no id"}]}`, `{"sensors": "none"}`, `null`} {
		if resp, body := f.do("PUT", "/houses/example/api/house", f.tokens["example"], bad); resp.StatusCode != 400 {
			t.Errorf("PUT house %s: %s %s", bad, resp.Status, body)
		}
	}
}

func TestHistory(t *testing.T) {
	f := newFixture(t)
	t0 := now()
	var batch []string
	for i := range 30 { // a reading a minute for the last half hour, the newest 10 the house's own
		batch = append(batch, strings.Replace(reading("office", t0-float64(60*i), 20), `"simulated": true`,
			fmt.Sprintf(`"simulated": %v`, i >= 10), 1))
	}
	f.send("example", batch...)
	history := func(query string) (resp *http.Response, h struct {
		Since, Until, Bucket float64
		Series               map[string][][3]*float64
	}) {
		resp, body := f.get("/houses/example/api/history" + query)
		if resp.StatusCode == 200 {
			if err := json.Unmarshal(body, &h); err != nil {
				t.Fatal(err)
			}
		}
		return resp, h
	}

	_, h := history("?hours=6")
	if h.Bucket != 60 || len(h.Series["office"]) != 30 || h.Until-h.Since != 6*3600 {
		t.Errorf("6 hours: bucket %v, %d points, %v s", h.Bucket, len(h.Series["office"]), h.Until-h.Since)
	}
	if _, h = history("?hours=6&simulated=0"); len(h.Series["office"]) != 10 {
		t.Errorf("without simulated readings: %d points", len(h.Series["office"]))
	}
	// The bucket sizes the Python server gives, as tests/test_server.py pins them: at most 500 points per sensor.
	for query, bucket := range map[string]float64{"": 3 * 60, "?hours=720": 87 * 60, "?hours=0.5": 60} {
		if _, h = history(query); h.Bucket != bucket {
			t.Errorf("%q: bucket %v, want %v", query, h.Bucket, bucket)
		}
	}
	for _, hours := range []string{"0", "-1", "nan", "inf", "8785", "", "a day"} {
		if resp, _ := history("?hours=" + hours); resp.StatusCode != 400 {
			t.Errorf("hours=%s: %s", hours, resp.Status)
		}
	}
}

// A stream as the page reads it: its events' data, one by one.
type stream struct {
	t      *testing.T
	body   io.ReadCloser
	events chan string
}

func (f *fixture) stream(id string) *stream {
	f.t.Helper()
	resp, err := http.Get(f.url + "/houses/" + id + "/api/stream")
	if err != nil {
		f.t.Fatal(err)
	}
	if resp.StatusCode != 200 || resp.Header.Get("Content-Type") != "text/event-stream" {
		f.t.Fatalf("stream: %s %s", resp.Status, resp.Header.Get("Content-Type"))
	}
	s := &stream{f.t, resp.Body, make(chan string, 100)}
	f.t.Cleanup(func() { resp.Body.Close() })
	go func() {
		defer close(s.events)
		lines := bufio.NewScanner(resp.Body)
		for lines.Scan() {
			if line := lines.Text(); line != "" {
				s.events <- line
			}
		}
	}()
	if first := s.next(); first != "retry: 3000" {
		f.t.Fatalf("the stream began %q", first)
	}
	return s
}

// next is the stream's next line with something on it, or "" if it ended.
func (s *stream) next() string {
	s.t.Helper()
	select {
	case line := <-s.events:
		return line
	case <-time.After(5 * time.Second):
		s.t.Fatal("nothing on the stream")
		return ""
	}
}

func (s *stream) nextReading() (sensor string, ts float64) {
	s.t.Helper()
	line := s.next()
	data, ok := strings.CutPrefix(line, "data: ")
	if !ok {
		s.t.Fatalf("got %q, want a reading", line)
	}
	var r struct {
		SensorID string `json:"sensor_id"`
		TS       float64
	}
	if err := json.Unmarshal([]byte(data), &r); err != nil {
		s.t.Fatal(err)
	}
	return r.SensorID, r.TS
}

func TestTheStreamSendsOnlyNews(t *testing.T) {
	f := newFixture(t)
	page := f.stream("example")
	t0 := now()
	batch := []string{reading("office", t0, 20), reading("kitchen", t0-30, 21)}
	f.send("example", batch...)
	// In time order, whatever the batch's order.
	if sensor, ts := page.nextReading(); sensor != "kitchen" || ts != t0-30 {
		t.Errorf("first: %s at %v", sensor, ts)
	}
	if sensor, _ := page.nextReading(); sensor != "office" {
		t.Errorf("second: %s", sensor)
	}
	f.send("example", batch...)                      // sent again: nothing new to show
	f.send("mine", reading("office", t0+1, 30))      // another house's
	f.send("example", reading("office", t0+1, 20.5)) // news
	if sensor, ts := page.nextReading(); sensor != "office" || ts != t0+1 {
		t.Errorf("after a batch sent again, got %s at %v; want only the new reading", sensor, ts)
	}
}

func TestShuttingDownEndsStreams(t *testing.T) {
	f := newFixture(t)
	page := f.stream("example")
	f.api.Close()
	select {
	case _, open := <-page.events:
		if open {
			t.Error("the stream carried on")
		}
	case <-time.After(5 * time.Second):
		t.Error("the stream didn't end")
	}
}
