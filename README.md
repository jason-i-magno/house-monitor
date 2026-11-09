# house-monitor

Live temperature and humidity for every room, in a web app you run at home.

For now the readings come from simulated sensors in a house: an example two-story house over a basement, with warm rooms upstairs, a cold basement, the thermostat on the main floor and a space heater in the office during the work day, or a house file of your own. Rooms with an ecobee thermostat or SmartSensor can read from it instead (see [ecobee sensors](#ecobee-sensors)). Real Govee H5075 Bluetooth sensors come next.

It uses only the Python standard library (Python 3.10 or newer) and plain HTML, CSS and JavaScript, with no build step.

## Running it

```sh
python3 -m housemon                  # then open http://127.0.0.1:8000/
python3 -m housemon --host 0.0.0.0   # also reachable from your phone on the home network
python3 -m unittest                  # tests
```

The first start fills an empty database with 24 hours of simulated history, so the charts have something to show. Readings are kept in `data/simulated.sqlite3` (gitignored); delete it to start over.

| Option | Default | |
|---|---|---|
| `--port` | 8000 | |
| `--host` | 127.0.0.1 | `0.0.0.0` serves the whole network. There is no login, so keep it off the internet. |
| `--db` | `data/<source>.sqlite3` | |
| `--house` | `private/house.py` if it's there, else the example | The house file the simulator models. |
| `--interval` | 10 | Seconds between simulated readings from each sensor (real H5075s report about once a minute). |
| `--store-every` | 60 | Seconds between stored readings per sensor. |
| `--backfill-hours` | 24 | Simulated history to create in a new database; 0 for none. |
| `--dump` | | Print the house as the page gets it, with its walls worked out, and exit. |
| `--ecobee` | `data/ecobee.json` | The saved ecobee sign-in. |
| `--ecobee-sign-in` | | Sign in to ecobee, save it to `--ecobee`, list its sensors, and exit. |

## ecobee sensors

A room with an ecobee thermostat or SmartSensor in it can show that sensor's readings in place of simulated ones. Sign in once; it asks for your ecobee email, password and, if your account has one, the code from your second factor:

```sh
python3 -m housemon --ecobee-sign-in
```

It saves only the refresh token ecobee gives, to `data/ecobee.json` (readable only by you, and gitignored), never your password, and lists the sensors it found. In your house file, give the room the sensor's name as the ecobee app shows it, as `SimulatedRoom(..., ecobee="Dining Room")`, and start the app. It polls ecobee every three minutes. A SmartSensor reads temperature but not humidity, so that room shows no humidity. ecobee no longer gives out developer API keys, so this signs in as ecobee's own web app does, as Home Assistant's ecobee integration does; it isn't an official API, and could stop working if ecobee changes its sign-in. If the sign-in lapses, the terminal says so; sign in again.

## Your own house

The simulator models a house file: `housemon/example_house.py` unless you give it your own. Copy the example to `private/house.py`, which this repo ignores, so you can keep your house in a private repo of its own there, and the app uses it in place of the example. `python3 -m unittest` runs the checks every house file must pass on it too, and any `test_*.py` you keep beside it. The server picks up changes to your house file when you reload the page, with no restart; if it doesn't load, the error goes to the terminal and the last version stays.

A house file has three parts:

- `HOUSE`: the rooms, each a simulated sensor with how it runs (its offset from the thermostat, how much of the weather reaches it, showers, cooking, a space heater) and its `Plan`. A plan places the room in feet: x to the right, y toward the front, its floor's elevation above the main floor, and its height at the ceiling's highest, 8 ft unless given. A sloped ceiling also has a `drop`: the wall it slopes down to and how much lower it is there. A room that isn't a rectangle, like an L, has a tuple of plans, one per piece.
- `AREAS`: the parts of the house with no sensor, which the map draws as outlines.
- `DETAILS`: what the map's detailed view draws (`housemon/details.py`): the doors, windows, vents, appliances, plumbing, fireplaces and closets, the roof, and outside the lot's outline, porches, decks, steps, paving, beds, trees, sheds, fences and sprinklers, each marked as known or a guess, and the house's own colors. A fixture or flight of steps can stand `against` the walls on sides of its room, and an opening can run from or to a wall (an offset or width of `None`), so they stay put against the walls wherever those end up.

## How it works

```
source ──► Hub ──► Store (SQLite)
                └► every open page (Server-Sent Events)
```

- **Source** (`simulator.py`, with the house from `house.py`): anything with a `sensors` list and a `run(publish, stop)` method that calls `publish(reading)` for each reading until `stop` is set. Every simulated value is a pure function of the room and the time, so the backfilled history matches the live readings.
- **Hub** (`hub.py`): keeps the latest reading from each sensor, hands each one to the store, and pushes it to every connected page. A page that stops reading misses readings rather than holding up the others.
- **Store** (`store.py`): stores at most one reading per sensor per `--store-every` seconds, since sensors broadcast far more often than room temperatures change. History is averaged into buckets so a chart gets at most 500 points per room for any range.
- **Server** (`server.py`):
  - `GET /`: the page.
  - `GET /api/sensors`: each room, where it is in the house, and its latest reading, plus the parts of the house with no sensor and the details the map's detailed view draws, with where its walls stand (`layout.py`).
  - `GET /api/history?hours=24`: bucketed history.
  - `GET /api/stream`: a Server-Sent Events stream with one JSON reading per message.
- **Page** (`web/`):
  - Room cards grouped by floor. A card is flagged if its room goes 5 minutes without a reading.
  - A house map: a floor plan of each level, or a 3D view of the house you can drag, or turn with shift and the arrow keys, to turn and tilt, from any side, above or below, with each room colored by its temperature on a gradient (blue colder, red warmer, through gray at a comfortable 68 °F) and the outside temperature in its corner and as the tint of its background. Its detailed view shows the house as it looks, with its doors, windows, vents, appliances, closets, roof and yard: whole from outside, or cut away to any level, and zoomed with the scroll wheel, about the pointer, with the + and − keys, or in on whatever you double-click, and panned by dragging with shift or the right button held, or with two fingers on a touch screen. The button beside the views makes the map full screen.
  - Temperature and humidity charts for 6 hours to 30 days, with a hover or keyboard readout and a table view. Unheated spaces like the garage get a temperature chart of their own. Click a room in a chart's legend to hide or show its line.
  - Light and dark mode follow the system setting.

Readings are stored in the sensors' own units (°C and percent relative humidity); the page converts to °F.

## Checking the map

Two tools in `tools/` help check changes to the house map, with Node 22 or newer:

```sh
node tools/scan.mjs [house file]   # faces in the detailed view that would flicker against each other
node tools/shot.mjs --view detailed --level Main --top --room Kitchen kitchen.png  # a screenshot of a running server's map
```

`scan.mjs` builds the detailed view without a browser or server. `shot.mjs` needs Chromium (`$CHROME`, or one on the PATH or from Playwright); its options are listed at the top of the file.

## Next: real sensors

A `GoveeSource` will listen for the H5075's Bluetooth broadcasts with [bleak](https://github.com/hbldh/bleak) (the first third-party dependency). It will decode temperature, humidity and battery, and map each sensor's Bluetooth address to a room. Everything downstream of the source stays the same.

## License

MIT. See [LICENSE](LICENSE).
