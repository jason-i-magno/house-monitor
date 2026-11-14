// Package store keeps houses and their readings in Postgres.
package store

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"errors"
	"fmt"
	"math"
	"slices"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jason-i-magno/house-monitor/cloud/house"
)

var (
	ErrNotFound     = errors.New("no such house")
	ErrUnauthorized = errors.New("not this house's token")
)

// A Store is the database, shared by every request.
type Store struct {
	db *pgxpool.Pool
}

// Open connects to the database at url, as a connection string or URL, and brings its schema up to date.
func Open(ctx context.Context, url string) (*Store, error) {
	config, err := pgxpool.ParseConfig(url)
	if err != nil {
		return nil, err
	}
	return OpenConfig(ctx, config)
}

// OpenConfig is Open, with the pool's config made already, as for tests to set a search_path of their own.
func OpenConfig(ctx context.Context, config *pgxpool.Config) (*Store, error) {
	db, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return nil, err
	}
	if err := Migrate(ctx, db); err != nil {
		db.Close()
		return nil, err
	}
	return &Store{db}, nil
}

func (s *Store) Close() { s.db.Close() }

// Ping checks the database answers, for a readiness check.
func (s *Store) Ping(ctx context.Context) error { return s.db.Ping(ctx) }

// A House is what's known of a house besides its layout and readings.
type House struct {
	ID     string
	Name   string
	Public bool // readable without signing in
}

// newToken makes a house's ingest token: hm_ and 128 random bits. The prefix lets a secret scanner, or a person,
// tell one from any other string.
func newToken() (token string, sum []byte) {
	token = "hm_" + rand.Text()
	hash := sha256.Sum256([]byte(token))
	return token, hash[:]
}

// AddHouse makes a house and returns its ingest token, which isn't kept, only its hash: it can't be shown again,
// only replaced (NewToken).
func (s *Store) AddHouse(ctx context.Context, h House) (string, error) {
	if !house.ValidID(h.ID) {
		return "", fmt.Errorf("a house's id is lower-case letters, digits and hyphens, not %q", h.ID)
	}
	token, sum := newToken()
	_, err := s.db.Exec(ctx, "INSERT INTO houses (id, name, public, token_sha256) VALUES ($1, $2, $3, $4)",
		h.ID, h.Name, h.Public, sum)
	return token, err
}

// NewToken replaces a house's ingest token, as when one has leaked. The old one stops working at once.
func (s *Store) NewToken(ctx context.Context, id string) (string, error) {
	token, sum := newToken()
	tag, err := s.db.Exec(ctx, "UPDATE houses SET token_sha256 = $2 WHERE id = $1", id, sum)
	if err == nil && tag.RowsAffected() == 0 {
		err = ErrNotFound
	}
	return token, err
}

// Authorize checks that token is the house's ingest token: ErrUnauthorized if it isn't, or there's no such house.
func (s *Store) Authorize(ctx context.Context, id, token string) error {
	var want []byte
	err := s.db.QueryRow(ctx, "SELECT token_sha256 FROM houses WHERE id = $1", id).Scan(&want)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrUnauthorized
	} else if err != nil {
		return err
	}
	got := sha256.Sum256([]byte(token))
	if subtle.ConstantTimeCompare(got[:], want) != 1 {
		return ErrUnauthorized
	}
	return nil
}

// House looks a house up by its id: ErrNotFound if there's none.
func (s *Store) House(ctx context.Context, id string) (House, error) {
	h := House{ID: id}
	err := s.db.QueryRow(ctx, "SELECT name, public FROM houses WHERE id = $1", id).Scan(&h.Name, &h.Public)
	if errors.Is(err, pgx.ErrNoRows) {
		err = ErrNotFound
	}
	return h, err
}

// SetLayout keeps the house as `python3 -m housemon --dump` prints it, JSON, in place of the one it had.
func (s *Store) SetLayout(ctx context.Context, id string, layout []byte) error {
	tag, err := s.db.Exec(ctx, "UPDATE houses SET layout = $2, layout_updated_at = now() WHERE id = $1", id, string(layout))
	if err == nil && tag.RowsAffected() == 0 {
		err = ErrNotFound
	}
	return err
}

// Layout is the house as it last sent it, or nil if it hasn't yet.
func (s *Store) Layout(ctx context.Context, id string) ([]byte, error) {
	var layout []byte
	err := s.db.QueryRow(ctx, "SELECT layout FROM houses WHERE id = $1", id).Scan(&layout)
	if errors.Is(err, pgx.ErrNoRows) {
		err = ErrNotFound
	}
	return layout, err
}

const readingColumns = "sensor_id, ts, temperature_c, humidity, battery, rssi, simulated"

// AddReadings stores readings, but those it has already, and returns the ones it stored, in time order.
//
// A house sends a batch again when it didn't hear back, though the first may have arrived, so a reading already
// stored, by its sensor and time, is skipped rather than counted twice, and only news is returned, to send on to
// open pages.
func (s *Store) AddReadings(ctx context.Context, houseID string, readings []house.Reading) ([]house.Reading, error) {
	if len(readings) == 0 {
		return nil, nil
	}
	// One array per column, unnested into rows, so a whole batch is one statement and one round trip.
	n := len(readings)
	sensors, times := make([]string, n), make([]time.Time, n)
	temps, humidities := make([]float64, n), make([]*float64, n)
	batteries, rssis, simulated := make([]*int32, n), make([]*int32, n), make([]bool, n)
	for i, r := range readings {
		sensors[i], times[i], temps[i], humidities[i] = r.SensorID, r.Time(), r.TemperatureC, r.Humidity
		batteries[i], rssis[i], simulated[i] = int32Ptr(r.Battery), int32Ptr(r.RSSI), r.Simulated
	}
	rows, err := s.db.Query(ctx, `
		INSERT INTO readings (house_id, `+readingColumns+`)
		SELECT $1, r.* FROM unnest($2::text[], $3::timestamptz[], $4::float8[], $5::float8[], $6::int[], $7::int[],
			$8::bool[]) AS r
		ON CONFLICT (house_id, sensor_id, ts) DO NOTHING
		RETURNING `+readingColumns,
		houseID, sensors, times, temps, humidities, batteries, rssis, simulated)
	if err != nil {
		return nil, err
	}
	stored, err := pgx.CollectRows(rows, scanReading)
	if err != nil {
		return nil, err
	}
	slices.SortStableFunc(stored, func(a, b house.Reading) int { return compareFloat(a.TS, b.TS) })
	return stored, nil
}

func int32Ptr(v *int) *int32 {
	if v == nil {
		return nil
	}
	i := int32(*v) // in range: ParseReading checked it
	return &i
}

func compareFloat(a, b float64) int {
	switch {
	case a < b:
		return -1
	case a > b:
		return 1
	}
	return 0
}

func scanReading(row pgx.CollectableRow) (house.Reading, error) {
	var r house.Reading
	var ts time.Time
	var battery, rssi *int32
	if err := row.Scan(&r.SensorID, &ts, &r.TemperatureC, &r.Humidity, &battery, &rssi, &r.Simulated); err != nil {
		return r, err
	}
	r.TS, r.Battery, r.RSSI = house.Seconds(ts), intPtr(battery), intPtr(rssi)
	return r, nil
}

func intPtr(v *int32) *int {
	if v == nil {
		return nil
	}
	i := int(*v)
	return &i
}

// Latest is the newest reading from each of the sensors that has one, by sensor id.
func (s *Store) Latest(ctx context.Context, houseID string, sensorIDs []string) (map[string]house.Reading, error) {
	// A lookup per sensor down the primary key's index, newest first, rather than a scan of all its readings.
	rows, err := s.db.Query(ctx, `
		SELECT `+readingColumns+` FROM unnest($2::text[]) AS sensor (id)
		CROSS JOIN LATERAL (
			SELECT `+readingColumns+` FROM readings WHERE house_id = $1 AND sensor_id = sensor.id
			ORDER BY ts DESC LIMIT 1
		) AS latest`,
		houseID, sensorIDs)
	if err != nil {
		return nil, err
	}
	readings, err := pgx.CollectRows(rows, scanReading)
	if err != nil {
		return nil, err
	}
	latest := make(map[string]house.Reading, len(readings))
	for _, r := range readings {
		latest[r.SensorID] = r
	}
	return latest, nil
}

// A Point is one bucket of a sensor's history, as /api/history sends it: [ts, temperature_c, humidity], the mean of
// each over the bucket's readings, humidity nil for a bucket with none.
type Point [3]*float64

// History is each sensor's readings since `since`, averaged into buckets of `bucket` that start at whole multiples
// of it from the Unix epoch, in time order; but the simulator's, unless `simulated`.
func (s *Store) History(ctx context.Context, houseID string, since time.Time, bucket time.Duration, simulated bool) (map[string][]Point, error) {
	rows, err := s.db.Query(ctx, `
		SELECT sensor_id, avg(extract(epoch FROM ts))::float8, avg(temperature_c), avg(humidity)
		FROM readings
		WHERE house_id = $1 AND ts >= $2 AND ($4 OR NOT simulated)
		GROUP BY sensor_id, date_bin(make_interval(secs => $3), ts, 'epoch')
		ORDER BY sensor_id, 2`,
		houseID, since, bucket.Seconds(), simulated)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	series := make(map[string][]Point)
	for rows.Next() {
		var sensor string
		var ts, temp float64
		var humidity *float64
		if err := rows.Scan(&sensor, &ts, &temp, &humidity); err != nil {
			return nil, err
		}
		// Rounded as the Python server rounds them, to what the page shows and no further.
		if humidity != nil {
			humidity = ptr(round(*humidity, 2))
		}
		series[sensor] = append(series[sensor], Point{ptr(round(ts, 1)), ptr(round(temp, 2)), humidity})
	}
	return series, rows.Err()
}

func round(x float64, places int) float64 {
	scale := math.Pow10(places)
	return math.Round(x*scale) / scale
}

func ptr[T any](v T) *T { return &v }
