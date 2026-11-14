package house

import (
	"strings"
	"testing"
	"time"
)

func TestParseReading(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	good := `{"sensor_id": "sim-office", "ts": 1800000000.25, "temperature_c": 21.3, "humidity": 36.5, "battery": 90,
		"rssi": -60, "simulated": true}`
	r, err := ParseReading([]byte(good), now)
	if err != nil {
		t.Fatal(err)
	}
	if r.SensorID != "sim-office" || r.TS != 1800000000.25 || r.TemperatureC != 21.3 || *r.Humidity != 36.5 ||
		*r.Battery != 90 || *r.RSSI != -60 || !r.Simulated {
		t.Errorf("parsed %+v", r)
	}

	// A sensor that reads only temperature, as an ecobee SmartSensor, sends no humidity, battery or signal.
	r, err = ParseReading([]byte(`{"sensor_id": "a", "ts": 1800000000, "temperature_c": -3.5, "humidity": null}`), now)
	if err != nil {
		t.Fatal(err)
	}
	if r.Humidity != nil || r.Battery != nil || r.RSSI != nil || r.Simulated {
		t.Errorf("parsed %+v", r)
	}

	refused := map[string]string{
		`[1, 2]`:                                                                    "not a reading",
		`{"sensor_id": "a", "ts": 1800000000}`:                                      "needs",
		`{"sensor_id": "a", "temperature_c": 20}`:                                   "needs",
		`{"ts": 1800000000, "temperature_c": 20}`:                                   "needs",
		`{"sensor_id": "a", "ts": 1800000000, "temperature_c": "warm"}`:             "not a reading",
		`{"sensor_id": "", "ts": 1800000000, "temperature_c": 20}`:                  "sensor_id",
		`{"sensor_id": "a", "ts": 0, "temperature_c": 20}`:                          "before 2000",
		`{"sensor_id": "a", "ts": 1e300, "temperature_c": 20}`:                      "ahead",
		`{"sensor_id": "a", "ts": 1800000301, "temperature_c": 20}`:                 "ahead",
		`{"sensor_id": "a", "ts": 1800000000, "temperature_c": 150}`:                "temperature_c",
		`{"sensor_id": "a", "ts": 1800000000, "temperature_c": 20, "humidity": -1}`: "humidity",
		`{"sensor_id": "a", "ts": 1800000000, "temperature_c": 20, "battery": 101}`: "battery",
		`{"sensor_id": "a", "ts": 1800000000, "temperature_c": 20, "rssi": 50}`:     "rssi",
		`{"sensor_id": "a", "ts": 1800000000, "temperature_c": 20, "battery": 1.5}`: "not a reading",
	}
	for raw, want := range refused {
		if _, err := ParseReading([]byte(raw), now); err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("ParseReading(%s) = %v, want an error saying %q", raw, err, want)
		}
	}

	long := `{"sensor_id": "` + strings.Repeat("x", 129) + `", "ts": 1800000000, "temperature_c": 20}`
	if _, err := ParseReading([]byte(long), now); err == nil {
		t.Error("a 129-byte sensor id was taken")
	}
	// Up to MaxSkew ahead is a clock a little fast, not a reading from the future.
	if _, err := ParseReading([]byte(`{"sensor_id": "a", "ts": 1800000299, "temperature_c": 20}`), now); err != nil {
		t.Error(err)
	}
}

func TestTimeKeepsMicroseconds(t *testing.T) {
	// The same ts must make the same key every time a batch is sent again, and come back as it went in.
	r := Reading{TS: 1800000000.123456}
	if got := r.Time(); got.UnixMicro() != 1800000000123456 {
		t.Errorf("Time() = %v", got.UnixMicro())
	}
	if got := Seconds(r.Time()); got != r.TS {
		t.Errorf("Seconds(Time()) = %v, want %v", got, r.TS)
	}
}

func TestValidID(t *testing.T) {
	for _, id := range []string{"example", "demo-2", "0"} {
		if !ValidID(id) {
			t.Errorf("ValidID(%q) = false", id)
		}
	}
	for _, id := range []string{"", "-a", "Example", "a b", "a/b", "..", strings.Repeat("a", 64)} {
		if ValidID(id) {
			t.Errorf("ValidID(%q) = true", id)
		}
	}
}
