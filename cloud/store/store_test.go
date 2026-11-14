package store_test

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/jason-i-magno/house-monitor/cloud/house"
	"github.com/jason-i-magno/house-monitor/cloud/store"
	"github.com/jason-i-magno/house-monitor/cloud/store/storetest"
)

func ptr[T any](v T) *T { return &v }

func addHouse(t *testing.T, s *store.Store, id string) string {
	t.Helper()
	token, err := s.AddHouse(context.Background(), store.House{ID: id, Name: id})
	if err != nil {
		t.Fatal(err)
	}
	return token
}

func TestCopiesStartingAtOnceMigrateOnce(t *testing.T) {
	config := storetest.Config(t)
	ctx := context.Background()
	errs := make(chan error)
	for range 4 { // as replicas starting together do
		go func() {
			s, err := store.OpenConfig(ctx, config.Copy())
			if err == nil {
				s.Close()
			}
			errs <- err
		}()
	}
	for range 4 {
		if err := <-errs; err != nil {
			t.Error(err)
		}
	}

	// And one started later finds the schema up to date, and its data kept.
	s, err := store.OpenConfig(ctx, config.Copy())
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	addHouse(t, s, "example")
	again, err := store.OpenConfig(ctx, config.Copy())
	if err != nil {
		t.Fatal(err)
	}
	defer again.Close()
	if _, err := again.House(ctx, "example"); err != nil {
		t.Error(err)
	}
}

func TestTokens(t *testing.T) {
	s := storetest.New(t)
	ctx := context.Background()
	token := addHouse(t, s, "example")
	other := addHouse(t, s, "other")
	if !strings.HasPrefix(token, "hm_") || len(token) < 20 {
		t.Errorf("token %q", token)
	}
	if err := s.Authorize(ctx, "example", token); err != nil {
		t.Error(err)
	}
	for _, try := range []struct{ house, token string }{
		{"example", other}, // another house's
		{"example", ""},
		{"example", token + "x"},
		{"nowhere", token},
	} {
		if err := s.Authorize(ctx, try.house, try.token); !errors.Is(err, store.ErrUnauthorized) {
			t.Errorf("Authorize(%q, %q) = %v", try.house, try.token, err)
		}
	}

	replaced, err := s.NewToken(ctx, "example")
	if err != nil {
		t.Fatal(err)
	}
	if err := s.Authorize(ctx, "example", token); !errors.Is(err, store.ErrUnauthorized) {
		t.Errorf("the replaced token still works: %v", err)
	}
	if err := s.Authorize(ctx, "example", replaced); err != nil {
		t.Error(err)
	}
	if _, err := s.NewToken(ctx, "nowhere"); !errors.Is(err, store.ErrNotFound) {
		t.Errorf("NewToken for no house = %v", err)
	}
}

func TestAddHouseRefusesBadIDs(t *testing.T) {
	s := storetest.New(t)
	if _, err := s.AddHouse(context.Background(), store.House{ID: "Bad ID"}); err == nil {
		t.Error("made a house with a bad id")
	}
}

func TestLayout(t *testing.T) {
	s := storetest.New(t)
	ctx := context.Background()
	addHouse(t, s, "example")
	if layout, err := s.Layout(ctx, "example"); err != nil || layout != nil {
		t.Errorf("Layout before one was sent = %s, %v", layout, err)
	}
	if err := s.SetLayout(ctx, "example", []byte(`{"sensors": [{"id": "a"}], "wall": 0.5}`)); err != nil {
		t.Fatal(err)
	}
	layout, err := s.Layout(ctx, "example")
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(layout, &got); err != nil || got["wall"] != 0.5 {
		t.Errorf("Layout = %s, %v", layout, err)
	}
	if err := s.SetLayout(ctx, "nowhere", []byte(`{}`)); !errors.Is(err, store.ErrNotFound) {
		t.Errorf("SetLayout for no house = %v", err)
	}
}

func TestAReadingSentTwiceIsStoredOnce(t *testing.T) {
	s := storetest.New(t)
	ctx := context.Background()
	addHouse(t, s, "example")
	addHouse(t, s, "other")
	now := float64(time.Now().Unix())
	batch := []house.Reading{
		{SensorID: "office", TS: now - 60.123456, TemperatureC: 21.3, Humidity: ptr(36.5), Battery: ptr(90), RSSI: ptr(-60)},
		{SensorID: "kitchen", TS: now - 120, TemperatureC: 20, Simulated: true},
	}
	stored, err := s.AddReadings(ctx, "example", batch)
	if err != nil {
		t.Fatal(err)
	}
	// Returned in time order, as they're sent on to pages, and as they went in.
	if len(stored) != 2 || stored[0].SensorID != "kitchen" || stored[1].TS != batch[0].TS ||
		*stored[1].Humidity != 36.5 || *stored[1].Battery != 90 || *stored[1].RSSI != -60 || !stored[0].Simulated {
		t.Errorf("stored %+v", stored)
	}

	// The same batch again, as after a reply that was lost, with one reading that's news.
	again := append(batch, house.Reading{SensorID: "office", TS: now, TemperatureC: 21.4})
	stored, err = s.AddReadings(ctx, "example", again)
	if err != nil {
		t.Fatal(err)
	}
	if len(stored) != 1 || stored[0].TS != now {
		t.Errorf("sent again, stored %+v; want only the new one", stored)
	}

	// Another house's sensor can share an id and a moment.
	if stored, err := s.AddReadings(ctx, "other", batch[:1]); err != nil || len(stored) != 1 {
		t.Errorf("another house's reading: %v, %v", stored, err)
	}

	latest, err := s.Latest(ctx, "example", []string{"office", "kitchen", "garage"})
	if err != nil {
		t.Fatal(err)
	}
	if len(latest) != 2 || latest["office"].TS != now || latest["kitchen"].TS != batch[1].TS {
		t.Errorf("Latest = %+v", latest)
	}
	if latest["office"].Humidity != nil {
		t.Error("the newest office reading has no humidity")
	}
}

func TestHistoryAveragesBuckets(t *testing.T) {
	s := storetest.New(t)
	ctx := context.Background()
	addHouse(t, s, "example")
	start := float64(time.Now().Add(-time.Hour).Unix()/600*600) + 600 // a whole 10 minutes, under an hour ago
	var readings []house.Reading
	for i := range 20 { // a reading a minute for 20 minutes: two 10-minute buckets
		readings = append(readings, house.Reading{
			SensorID: "office", TS: start + float64(60*i), TemperatureC: 20 + float64(i/10), Humidity: ptr(40.0),
		})
		readings = append(readings, house.Reading{
			SensorID: "garage", TS: start + float64(60*i), TemperatureC: 5, Simulated: i >= 10,
		})
	}
	if _, err := s.AddReadings(ctx, "example", readings); err != nil {
		t.Fatal(err)
	}
	since := time.Unix(int64(start), 0).Add(-time.Minute)
	series, err := s.History(ctx, "example", since, 10*time.Minute, true)
	if err != nil {
		t.Fatal(err)
	}
	office := series["office"]
	if len(office) != 2 {
		t.Fatalf("office = %v", office)
	}
	for i, want := range []struct{ ts, temp float64 }{{start + 270, 20}, {start + 870, 21}} {
		if *office[i][0] != want.ts || *office[i][1] != want.temp || *office[i][2] != 40 {
			t.Errorf("office[%d] = %v, %v, %v; want %v, %v, 40", i, *office[i][0], *office[i][1], *office[i][2], want.ts, want.temp)
		}
	}
	if len(series["garage"]) != 2 || series["garage"][0][2] != nil {
		t.Errorf("garage = %v; want two buckets with no humidity", series["garage"])
	}

	// Without the simulator's readings, the garage's second bucket goes.
	series, err = s.History(ctx, "example", since, 10*time.Minute, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(series["garage"]) != 1 || len(series["office"]) != 2 {
		t.Errorf("without simulated readings: %d garage buckets, %d office", len(series["garage"]), len(series["office"]))
	}

	// Only since `since`.
	series, err = s.History(ctx, "example", time.Unix(int64(start)+600, 0), 10*time.Minute, true)
	if err != nil {
		t.Fatal(err)
	}
	if len(series["office"]) != 1 {
		t.Errorf("since the second bucket: %v", series["office"])
	}
}
