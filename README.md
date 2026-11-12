# house-monitor

Live temperature and humidity for every room, in a web app you run at home.

For now the readings come from simulated sensors in a house: an example two-story house over a basement, with warm rooms upstairs, a cold basement, the thermostat on the main floor and a space heater in the office during the work day, or a house file of your own. Rooms with an ecobee thermostat or SmartSensor can read from it instead (see [ecobee sensors](#ecobee-sensors)), and rooms with a Govee H5075 Bluetooth thermo-hygrometer from that (see [Govee sensors](#govee-sensors)). A MOVA robot vacuum can show what it's doing and its battery (see [MOVA vacuums](#mova-vacuums)).

It uses only the Python standard library (Python 3.10 or newer) and plain HTML, CSS and JavaScript, with no build step. Hearing Govee sensors takes one more package, [bleak](https://github.com/hbldh/bleak); without it, everything else works.

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
| `--interval` | 10 | Seconds between simulated readings from each sensor. |
| `--store-every` | 60 | Seconds between stored readings per sensor. |
| `--backfill-hours` | 24 | Simulated history to create in a new database; 0 for none. |
| `--dump` | | Print the house as the page gets it, with its walls worked out, and exit. |
| `--ecobee` | `data/ecobee.json` | The saved ecobee sign-in. |
| `--ecobee-sign-in` | | Sign in to ecobee, save it to `--ecobee`, list its sensors, and exit. |
| `--mova` | `data/mova.json` | The saved MOVAhome sign-in. |
| `--mova-sign-in` | | Sign in to MOVAhome, save it to `--mova`, list its vacuums, and exit. |
| `--govee-scan` | | List the Govee sensors heard over Bluetooth, with their readings, and exit. |

## ecobee sensors

A room with an ecobee thermostat or SmartSensor in it can show that sensor's readings in place of simulated ones. Sign in once; it asks for your ecobee email, password and, if your account has one, the code from your second factor:

```sh
python3 -m housemon --ecobee-sign-in
```

It saves only the refresh token ecobee gives, to `data/ecobee.json` (readable only by you, and gitignored), never your password, and lists the sensors it found. In your house file, give the room the sensor's name as the ecobee app shows it, as `SimulatedRoom(..., ecobee="Dining Room")`, and start the app. The outside can read the weather ecobee reports where a thermostat is, its area's temperature and humidity now from a weather service, rather than a sensor at your house: give it the thermostat's name, as `ecobee_weather="Hallway"`. It polls ecobee every three minutes. A SmartSensor reads temperature but not humidity, so that room shows no humidity. ecobee no longer gives out developer API keys, so this signs in as ecobee's own web app does, as Home Assistant's ecobee integration does; it isn't an official API, and could stop working if ecobee changes its sign-in. If the sign-in lapses, the terminal says so, and it stops asking until you sign in again, which it notices without a restart. If a poll fails, as when the internet is down, it waits longer before each try, up to 10 minutes, until one works. If a room names a sensor your account doesn't have, the terminal says that too, and lists the names it does have.

## Govee sensors

A room with a Govee H5075 in it can show that sensor's temperature, humidity, battery and signal. The H5075 keeps broadcasting its reading over Bluetooth, so there's no app, account or pairing: the app just listens, on the computer it runs on, which needs Bluetooth and to be in range of each sensor. Install bleak, turn Bluetooth on, and list the sensors it hears:

```sh
python3 -m pip install -r requirements.txt   # bleak
python3 -m housemon --govee-scan
```

Each is named `GVH5075_` and the last four characters of its Bluetooth address. To tell them apart, hold one in your hand for a minute and scan again: its temperature rises. In your house file, give the room its name, as `SimulatedRoom(..., govee="GVH5075_1A2B")`, and start the app. Each room takes its sensor's newest reading every 10 seconds. If a sensor isn't heard from for 5 minutes, the terminal says so, with the names it does hear; if Bluetooth is off, it says that, and tries again every minute. Without bleak, the terminal notes that those rooms stay simulated.

A room can have an ecobee SmartSensor beside its Govee, named as both `ecobee=` and `govee=`, say to keep the SmartSensor steering the thermostat while the Govee adds humidity. The room shows the Govee's readings, and the SmartSensor's only while the Govee goes unheard for 2 minutes, as with a dead battery or Bluetooth off. The outside can do the same with a thermostat's weather.

## Simulated readings

The rest of the house stays simulated. The page's **Hide simulated** button, beside the connection status, leaves the simulator's readings out: their rooms' cards, their values on the map and their lines in the charts, along with the made-up past of a room that now reads from a real sensor, so you see only real readings.

## MOVA vacuums

A MOVA robot vacuum can have a card of its own on the page, with what it's doing (vacuuming, returning to its dock, charging, an error) and its battery; while it's cleaning, how far along it is, with how long it's been at it and how much it's cleaned; and what needs looking at: an error or warning in words, as "Wheels are suspended", what's wrong at its dock (its water tanks, dust bag and detergent), and the parts it counts down that are at 15% or less, the brushes, filter and mop pads to replace and the sensors and wheels to clean. Sign in once; it asks for your MOVAhome region (us, eu, cn, sg, ru or in), email and password:

```sh
python3 -m housemon --mova-sign-in
```

If you sign in to MOVAhome with Apple or Google, set a password in the app first. It saves only the refresh token MOVA gives, to `data/mova.json` (readable only by you, and gitignored), never your email or password, and lists the vacuums it found. Then start the app: it asks each vacuum on the account how it is every minute. MOVA has no public API, so this signs in as the MOVAhome app does, as the community [Dreame Vacuum integration](https://github.com/Tasshack/dreame-vacuum) for Home Assistant worked out, with the app's own client id and secret, which are the same for every MOVAhome user. It isn't official, and could stop working if MOVA changes its app; the newest apps connect in a way of their own, which this doesn't copy. If the sign-in lapses, the terminal says so, and it stops asking until you sign in again, which it notices without a restart. If a poll fails, as when the internet is down, it waits longer before each try, up to 10 minutes, until one works.

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

- **Source** (`simulator.py`, with the house from `house.py`, beside `ecobee.py` and `govee.py` for the rooms with real sensors): anything with a `sensors` list and a `run(publish, stop)` method that calls `publish(reading)` for each reading until `stop` is set. Every simulated value is a pure function of the room and the time, so the backfilled history matches the live readings.
- **Hub** (`hub.py`): keeps the latest reading from each sensor, hands each one to the store, and pushes it to every connected page. A page that stops reading misses readings rather than holding up the others. It keeps what each robot vacuum last said too (`mova.py`), and pushes that to the pages, but doesn't store it.
- **Store** (`store.py`): stores at most one reading per sensor per `--store-every` seconds, since sensors broadcast far more often than room temperatures change. History is averaged into buckets so a chart gets at most 500 points per room for any range.
- **Server** (`server.py`):
  - `GET /`: the page.
  - `GET /api/sensors`: each room, where it is in the house, and its latest reading, plus the parts of the house with no sensor and the details the map's detailed view draws, with where its walls stand (`layout.py`), and what each robot vacuum is doing.
  - `GET /api/history?hours=24`: bucketed history.
  - `GET /api/stream`: a Server-Sent Events stream with one JSON reading per message, and a `vacuum` event with what a robot vacuum is doing whenever it says.
- **Page** (`web/`):
  - Room cards grouped by floor, and a card for each robot vacuum, with its battery, what it's doing, how far along a clean is, and what needs looking at. A card is flagged if its room, or vacuum, goes 5 minutes without a reading.
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

## License

MIT. See [LICENSE](LICENSE).
