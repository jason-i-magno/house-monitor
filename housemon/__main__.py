"""Run the house monitor: python3 -m housemon"""

import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path

from . import ecobee, govee, mova, uplink, whisker
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
        "--whisker",
        type=Path,
        default=Path("data") / "whisker.json",
        help="the saved Whisker sign-in: with it, litter boxes the house file names a Litter-Robot for read their state from"
        " that robot (default: %(default)s)",
    )
    parser.add_argument(
        "--whisker-sign-in",
        action="store_true",
        help="sign in to Whisker, for a Litter-Robot, save the sign-in to --whisker, list its robots, and exit",
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
    parser.add_argument(
        "--uplink",
        type=Path,
        default=Path("data") / "uplink.json",
        help="the house in the cloud service (cloud/) and its token: with it, the house and its readings are sent there"
        " (default: %(default)s)",
    )
    parser.add_argument(
        "--uplink-sign-in",
        action="store_true",
        help="set up the uplink: ask for the house's URL in the cloud service and its token, check it, save it to --uplink,"
        " and exit",
    )
    parser.add_argument(
        "--govee-scan",
        action="store_true",
        help="listen for Govee H5075 sensors over Bluetooth, list those heard with their readings, and exit (needs bleak)",
    )
    args = parser.parse_args(argv)

    if args.ecobee_sign_in:
        try:
            ecobee.sign_in_interactively(args.ecobee)
        except ecobee.EcobeeError as e:
            raise SystemExit(f"Couldn't sign in to ecobee: {e}") from None
        return
    if args.whisker_sign_in:
        try:
            whisker.sign_in_interactively(args.whisker)
        except whisker.WhiskerError as e:
            raise SystemExit(f"Couldn't sign in to Whisker: {e}") from None
        return
    if args.mova_sign_in:
        try:
            mova.sign_in_interactively(args.mova)
        except (mova.MovaError, OSError) as e:
            raise SystemExit(f"Couldn't sign in to MOVAhome: {e}") from None
        return
    if args.uplink_sign_in:
        try:
            uplink.sign_in_interactively(args.uplink)
        except uplink.UplinkError as e:
            raise SystemExit(f"Couldn't set up the uplink: {e}") from None
        return

    house_file = houses.HouseFile(args.house)
    house = house_file.module
    source = SimulatedSource(house.HOUSE, args.interval, getattr(house, "AREAS", ()), getattr(house, "DETAILS", None))
    if args.dump:
        print(json.dumps(house_json(source.sensors, source.areas, source.details)))
        return

    def govee_rooms() -> dict[str, str]:
        return {room.govee: room.sensor.id for room in source.rooms if getattr(room, "govee", None)}

    bluetooth = govee.available()
    if args.govee_scan:
        if not bluetooth:
            raise SystemExit(f"Listening for Govee sensors takes bleak: {sys.executable} -m pip install bleak")
        try:
            govee.scan_interactively({room.govee: room.sensor.name for room in source.rooms if getattr(room, "govee", None)})
        except Exception as e:
            raise SystemExit(f"Couldn't listen for Govee sensors: {e or type(e).__name__}") from None
        return
    if govee_rooms() and not bluetooth:
        print(
            f"govee: rooms name Govee sensors, but hearing them takes bleak ({sys.executable} -m pip install bleak), so"
            " they're simulated",
            file=sys.stderr,
            flush=True,
        )

    # Signed in to ecobee, the rooms that name an ecobee sensor read from it, and an outside that names a thermostat
    # from its weather; with bleak, the rooms that name a Govee sensor read from it; and the simulator leaves them be.
    def ecobee_rooms() -> dict[str, str]:
        return {room.ecobee: room.sensor.id for room in source.rooms if getattr(room, "ecobee", None)}

    def ecobee_weather() -> dict[str, str]:
        return {room.ecobee_weather: room.sensor.id for room in source.rooms if getattr(room, "ecobee_weather", None)}

    def read_elsewhere() -> set[str]:
        ids = set(govee_rooms().values()) if bluetooth else set()
        if signed_in:
            ids |= set(ecobee_rooms().values()) | set(ecobee_weather().values())
        return ids

    signed_in = args.ecobee.exists()
    source.skip = read_elsewhere()

    # Signed in to Whisker, the litter boxes that name a Litter-Robot read from it, and the simulator leaves them be.
    def litter_robots() -> dict[str, str]:
        return source.details.litter_robots() if source.details else {}

    with_whisker = args.whisker.exists()
    source.skip_boxes = set(litter_robots().values()) if with_whisker else set()

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
            source.skip = read_elsewhere()
            source.areas = list(getattr(changed, "AREAS", ()))
            source.details = getattr(changed, "DETAILS", None)
            source.skip_boxes = set(litter_robots().values()) if with_whisker else set()
            server.sensors, server.areas, server.details = source.sensors, source.areas, source.details

    server = HouseServer(
        (args.host, args.port), hub, store, source.sensors, getattr(source, "areas", []), getattr(source, "details", None), refresh
    )
    stop = threading.Event()
    collectors = [threading.Thread(target=source.run, args=(hub.publish, stop), name="collector", daemon=True)]
    # A room with a Govee sensor and an ecobee one reads the Govee, and the ecobee only while the Govee is quiet.
    publish_ecobee = hub.publish
    if bluetooth:
        listener = govee.GoveeSource(govee_rooms)
        collectors.append(threading.Thread(target=listener.run, args=(hub.publish, stop), name="govee", daemon=True))
        publish_ecobee = govee.unless_heard(hub.publish, listener)
    if signed_in:
        poller = ecobee.EcobeeSource(ecobee.Account(args.ecobee), ecobee_rooms, weather=ecobee_weather)
        collectors.append(threading.Thread(target=poller.run, args=(publish_ecobee, stop), name="ecobee", daemon=True))
    if with_whisker:
        robots = whisker.WhiskerSource(whisker.Account(args.whisker), litter_robots)
        collectors.append(threading.Thread(target=robots.run, args=(hub.publish, stop), name="whisker", daemon=True))
    vacuums = args.mova.exists()
    if vacuums:
        poller = mova.MovaSource(mova.Account(args.mova))
        collectors.append(threading.Thread(target=poller.run, args=(hub.publish_vacuum, stop), name="mova", daemon=True))
    # Set up, the uplink sends the house, as it is when each round starts, and the readings the store keeps.
    def house_now() -> dict:
        refresh()
        return house_json(source.sensors, source.areas, source.details)

    sending = args.uplink.exists()
    if sending:
        sender = uplink.Uplink(args.uplink, store, house_now)
        collectors.append(threading.Thread(target=sender.run, args=(stop,), name="uplink", daemon=True))
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
    if with_whisker:
        sources += f", Litter-Robots ({', '.join(sorted(litter_robots(), key=str.casefold)) or 'no litter box names one yet'})"
    if bluetooth and govee_rooms():
        sources += f", Govee sensors ({', '.join(sorted(govee_rooms(), key=str.casefold))})"
    if vacuums:
        sources += ", MOVA vacuums"
    if sending:
        try:
            sources += f", sending to {uplink.Target.load(args.uplink).url}"
        except uplink.UplinkError:
            sources += f", sending as {args.uplink} says once it can be read"
    try:
        # Stopped as a container is, or by a service manager, it shuts down as from ^C. Without this, run as a
        # container's first process it wouldn't stop at all, as the kernel gives that one no default action for SIGTERM,
        # until killed. Before saying it's up, so whatever waits for that to stop it is sure to stop it cleanly.
        signal.signal(signal.SIGTERM, _interrupt)
        print(f"House monitor on http://{args.host}:{server.server_port}/ ({sources}, {file}, {db})", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for collector in collectors:
            collector.join()
        server.server_close()
        store.close()


def _interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    main()
