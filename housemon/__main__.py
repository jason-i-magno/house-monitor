"""Run the house monitor: python3 -m housemon"""

import argparse
import json
import threading
import time
from pathlib import Path

from . import ecobee, mova
from . import house as houses
from .hub import Hub
from .server import HouseServer, house_json
from .simulator import SimulatedSource
from .store import Store


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python3 -m housemon",
        description="Track temperature and humidity around the house and show them live in a web app.",
    )
    parser.add_argument("--source", choices=["simulated"], default="simulated", help="where readings come from")
    parser.add_argument("--db", type=Path, help="SQLite database (default: data/<source>.sqlite3)")
    parser.add_argument(
        "--house", type=Path, help="house file the simulator models (default: private/house.py if it's there, else the example)"
    )
    parser.add_argument("--host", default="127.0.0.1", help="address to serve on; 0.0.0.0 to reach it from a phone")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--interval", type=float, default=10.0, help="seconds between simulated readings per sensor")
    parser.add_argument("--store-every", type=float, default=60.0, help="seconds between stored readings per sensor")
    parser.add_argument(
        "--backfill-hours", type=float, default=24.0, help="hours of simulated history to create in a new database"
    )
    parser.add_argument(
        "--dump", action="store_true", help="print the house as /api/sensors sends it, without readings, and exit"
    )
    parser.add_argument(
        "--ecobee",
        type=Path,
        default=Path("data") / "ecobee.json",
        help="the saved ecobee sign-in: with it, rooms the house file gives an ecobee sensor read from that sensor, and an"
        " outside it gives a thermostat's weather from that (default: %(default)s)",
    )
    parser.add_argument(
        "--ecobee-sign-in", action="store_true", help="sign in to ecobee, save the sign-in to --ecobee, list its sensors, and exit"
    )
    parser.add_argument(
        "--mova",
        type=Path,
        default=Path("data") / "mova.json",
        help="the saved MOVAhome sign-in: with it, the page shows what each of the account's robot vacuums is doing and"
        " its battery (default: %(default)s)",
    )
    parser.add_argument(
        "--mova-sign-in", action="store_true", help="sign in to MOVAhome, save the sign-in to --mova, list its vacuums, and exit"
    )
    args = parser.parse_args(argv)

    if args.ecobee_sign_in:
        try:
            ecobee.sign_in_interactively(args.ecobee)
        except ecobee.EcobeeError as e:
            raise SystemExit(f"Couldn't sign in to ecobee: {e}") from None
        return
    if args.mova_sign_in:
        try:
            mova.sign_in_interactively(args.mova)
        except (mova.MovaError, OSError) as e:
            raise SystemExit(f"Couldn't sign in to MOVAhome: {e}") from None
        return

    house_file = houses.HouseFile(args.house)
    house = house_file.module
    source = SimulatedSource(house.HOUSE, args.interval, getattr(house, "AREAS", ()), getattr(house, "DETAILS", None))
    if args.dump:
        print(json.dumps(house_json(source.sensors, source.areas, source.details)))
        return

    # Signed in to ecobee, the rooms that name an ecobee sensor read from it, and an outside that names a thermostat
    # from its weather, and the simulator leaves them be.
    def ecobee_rooms() -> dict[str, str]:
        return {room.ecobee: room.sensor.id for room in source.rooms if getattr(room, "ecobee", None)}

    def ecobee_weather() -> dict[str, str]:
        return {room.ecobee_weather: room.sensor.id for room in source.rooms if getattr(room, "ecobee_weather", None)}

    def ecobee_read() -> set[str]:
        return set(ecobee_rooms().values()) | set(ecobee_weather().values())

    signed_in = args.ecobee.exists()
    if signed_in:
        source.skip = ecobee_read()

    db = args.db or Path("data") / f"{args.source}.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db, min_interval=args.store_every)
    if store.is_empty() and args.backfill_hours > 0:
        now = time.time()
        store.add_many(source.backfill(now - args.backfill_hours * 3600, now, args.store_every))

    hub = Hub(store)

    def refresh() -> None:
        """Take up the house file's changes, if it has any, before the page loads the house."""
        changed = house_file.changed()
        if changed:
            source.rooms = changed.HOUSE
            if signed_in:
                source.skip = ecobee_read()
            source.areas = list(getattr(changed, "AREAS", ()))
            source.details = getattr(changed, "DETAILS", None)
            server.sensors, server.areas, server.details = source.sensors, source.areas, source.details

    server = HouseServer(
        (args.host, args.port), hub, store, source.sensors, getattr(source, "areas", []), getattr(source, "details", None), refresh
    )
    stop = threading.Event()
    collectors = [threading.Thread(target=source.run, args=(hub.publish, stop), name="collector", daemon=True)]
    if signed_in:
        poller = ecobee.EcobeeSource(ecobee.Account(args.ecobee), ecobee_rooms, weather=ecobee_weather)
        collectors.append(threading.Thread(target=poller.run, args=(hub.publish, stop), name="ecobee", daemon=True))
    vacuums = args.mova.exists()
    if vacuums:
        poller = mova.MovaSource(mova.Account(args.mova))
        collectors.append(threading.Thread(target=poller.run, args=(hub.publish_vacuum, stop), name="mova", daemon=True))
    for collector in collectors:
        collector.start()
    file = Path(house.__file__).resolve()
    file = file.relative_to(Path.cwd()) if file.is_relative_to(Path.cwd()) else file
    sources = f"{args.source} sensors"
    if signed_in:
        names = ", ".join(sorted(ecobee_rooms())) or "no room names one yet"
        sources += f", ecobee sensors ({names})"
        if ecobee_weather():
            sources += f", weather where {', '.join(sorted(ecobee_weather()))} is"
    if vacuums:
        sources += ", MOVA vacuums"
    print(f"House monitor on http://{args.host}:{server.server_port}/ ({sources}, {file}, {db})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for collector in collectors:
            collector.join()
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
