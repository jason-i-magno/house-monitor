"""Run the house monitor: python3 -m housemon"""

import argparse
import threading
import time
from pathlib import Path

from . import house as houses
from .hub import Hub
from .server import HouseServer
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
    args = parser.parse_args(argv)

    db = args.db or Path("data") / f"{args.source}.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db, min_interval=args.store_every)
    house = houses.load(args.house)
    source = SimulatedSource(house.HOUSE, args.interval, getattr(house, "AREAS", ()), getattr(house, "DETAILS", None))
    if store.is_empty() and args.backfill_hours > 0:
        now = time.time()
        store.add_many(source.backfill(now - args.backfill_hours * 3600, now, args.store_every))

    hub = Hub(store)
    server = HouseServer(
        (args.host, args.port), hub, store, source.sensors, getattr(source, "areas", []), getattr(source, "details", None)
    )
    stop = threading.Event()
    collector = threading.Thread(target=source.run, args=(hub.publish, stop), name="collector", daemon=True)
    collector.start()
    file = Path(house.__file__).resolve()
    file = file.relative_to(Path.cwd()) if file.is_relative_to(Path.cwd()) else file
    print(f"House monitor on http://{args.host}:{server.server_port}/ ({args.source} sensors, {file}, {db})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        collector.join()
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
