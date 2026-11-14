// Package house has what the cloud service knows of a house: its id, and its sensors' readings, as the Python app
// produces them (Reading in housemon/readings.py).
package house

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"regexp"
	"time"
	"unicode/utf8"
)

// An id names a house in URLs, as /houses/example/: lower-case letters, digits and hyphens.
var idPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,62}$`)

// ValidID reports whether id can name a house.
func ValidID(id string) bool {
	return idPattern.MatchString(id)
}

// A Reading is one measurement, in the units the sensors themselves report, as Reading.to_json() in
// housemon/readings.py sends it, field for field.
type Reading struct {
	SensorID     string   `json:"sensor_id"`
	TS           float64  `json:"ts"` // Unix time, seconds
	TemperatureC float64  `json:"temperature_c"`
	Humidity     *float64 `json:"humidity"` // relative humidity, percent; nil from a sensor that reads only temperature
	Battery      *int     `json:"battery"`  // percent
	RSSI         *int     `json:"rssi"`     // Bluetooth signal strength, dBm
	Simulated    bool     `json:"simulated"`
}

// Time is when the reading was taken, to the microsecond, as Postgres keeps it.
func (r Reading) Time() time.Time {
	return time.UnixMicro(int64(math.Round(r.TS * 1e6)))
}

// Seconds is t as a Reading's TS: Unix time, in seconds.
func Seconds(t time.Time) float64 {
	return float64(t.UnixMicro()) / 1e6
}

const (
	maxSensorID = 128
	// MaxSkew is how far ahead of the server's clock a reading can be. One from further ahead would stay a sensor's
	// latest until the clocks met, so it's refused instead.
	MaxSkew = 5 * time.Minute
)

// The ranges a reading's values must fall in. They keep out garbage, as a sensor's error code read as a value,
// rather than judging what a room can be.
var (
	earliest = time.Date(2000, 1, 1, 0, 0, 0, 0, time.UTC)
	minTemp  = -100.0
	maxTemp  = 100.0
)

// The fields as they arrive, each a pointer, so one left out is told from one sent as zero.
type wire struct {
	SensorID     *string  `json:"sensor_id"`
	TS           *float64 `json:"ts"`
	TemperatureC *float64 `json:"temperature_c"`
	Humidity     *float64 `json:"humidity"`
	Battery      *int     `json:"battery"`
	RSSI         *int     `json:"rssi"`
	Simulated    bool     `json:"simulated"`
}

// ParseReading decodes one reading and checks it, against now for how far ahead of the server it can be.
//
// Readings come in batches, so each is parsed on its own: one bad reading is refused, and the rest of its batch is
// still stored, rather than the whole batch failing on it every time the house sends it again.
func ParseReading(raw json.RawMessage, now time.Time) (Reading, error) {
	var w wire
	if err := json.Unmarshal(raw, &w); err != nil {
		return Reading{}, fmt.Errorf("not a reading: %w", err)
	}
	if w.SensorID == nil || w.TS == nil || w.TemperatureC == nil {
		return Reading{}, errors.New("a reading needs sensor_id, ts and temperature_c")
	}
	r := Reading{*w.SensorID, *w.TS, *w.TemperatureC, w.Humidity, w.Battery, w.RSSI, w.Simulated}
	return r, r.check(now)
}

func (r Reading) check(now time.Time) error {
	switch {
	case r.SensorID == "" || len(r.SensorID) > maxSensorID || !utf8.ValidString(r.SensorID):
		return fmt.Errorf("sensor_id must be 1 to %d bytes of UTF-8", maxSensorID)
	// Compared as seconds, not as Time(), which a ts far out of range would overflow.
	case r.TS < Seconds(earliest):
		return fmt.Errorf("ts %v is before %d", r.TS, earliest.Year())
	case r.TS > Seconds(now.Add(MaxSkew)):
		return fmt.Errorf("ts %v is more than %v ahead of the server's clock", r.TS, MaxSkew)
	case !within(r.TemperatureC, minTemp, maxTemp):
		return fmt.Errorf("temperature_c %v is outside %v to %v", r.TemperatureC, minTemp, maxTemp)
	case r.Humidity != nil && !within(*r.Humidity, 0, 100):
		return fmt.Errorf("humidity %v is outside 0 to 100", *r.Humidity)
	case r.Battery != nil && !within(*r.Battery, 0, 100):
		return fmt.Errorf("battery %v is outside 0 to 100", *r.Battery)
	case r.RSSI != nil && !within(*r.RSSI, -200, 20):
		return fmt.Errorf("rssi %v is outside -200 to 20", *r.RSSI)
	}
	return nil
}

// within reports whether lo <= x <= hi. Written that way round, it's false for NaN, as x < lo || x > hi isn't.
func within[T int | float64](x, lo, hi T) bool {
	return lo <= x && x <= hi
}
