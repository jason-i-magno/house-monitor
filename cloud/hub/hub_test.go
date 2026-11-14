package hub

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/jason-i-magno/house-monitor/cloud/house"
)

func reading(sensor string, ts float64) house.Reading {
	return house.Reading{SensorID: sensor, TS: ts, TemperatureC: 20}
}

func receive(t *testing.T, c *Client) house.Reading {
	t.Helper()
	select {
	case m := <-c.Messages():
		data, ok := strings.CutPrefix(string(m), "data: ")
		if !ok || !strings.HasSuffix(data, "\n\n") {
			t.Fatalf("message %q isn't one Server-Sent Event", m)
		}
		var r house.Reading
		if err := json.Unmarshal([]byte(data), &r); err != nil {
			t.Fatal(err)
		}
		return r
	default:
		t.Fatal("no message")
		return house.Reading{}
	}
}

func TestPublishReachesEveryPageOnTheHouseOnly(t *testing.T) {
	h := New()
	a, b := h.Subscribe("one"), h.Subscribe("one")
	other := h.Subscribe("two")
	h.Publish("one", []house.Reading{reading("office", 1), reading("kitchen", 2)})
	for _, c := range []*Client{a, b} {
		if r := receive(t, c); r.SensorID != "office" {
			t.Errorf("first reading %+v", r)
		}
		if r := receive(t, c); r.SensorID != "kitchen" {
			t.Errorf("second reading %+v", r)
		}
	}
	if len(other.Messages()) != 0 {
		t.Error("another house's page got the readings")
	}
}

func TestUnsubscribedPagesGetNothing(t *testing.T) {
	h := New()
	c := h.Subscribe("one")
	h.Unsubscribe(c)
	h.Publish("one", []house.Reading{reading("office", 1)})
	if len(c.Messages()) != 0 {
		t.Error("an unsubscribed page got a reading")
	}
	if h.Clients("one") != 0 {
		t.Errorf("Clients = %d after the only one left", h.Clients("one"))
	}
}

func TestAPageThatFallsBehindIsDropped(t *testing.T) {
	h := New()
	slow, keeping := h.Subscribe("one"), h.Subscribe("one")
	batch := make([]house.Reading, Buffer)
	for i := range batch {
		batch[i] = reading("office", float64(i))
	}
	h.Publish("one", batch) // fills both buffers exactly
	for range Buffer {
		<-keeping.Messages()
	}
	h.Publish("one", batch[:1]) // one more than the slow page has room for
	select {
	case <-slow.Lagged():
	default:
		t.Fatal("the page with a full buffer wasn't dropped")
	}
	select {
	case <-keeping.Lagged():
		t.Fatal("a page keeping up was dropped")
	default:
	}
	if h.Clients("one") != 1 {
		t.Errorf("Clients = %d, want only the one keeping up", h.Clients("one"))
	}
	h.Unsubscribe(slow) // as its handler does when it ends; dropping it twice is harmless
	h.Publish("one", batch[:1])
	if r := receive(t, keeping); r.TS != 0 {
		t.Errorf("got %+v", r)
	}
}
