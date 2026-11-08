"""The controllers online on VATSIM or IVAO, from the networks' public data feeds (no account, no key):

- VATSIM: ``data.vatsim.net/v3/vatsim-data.json`` (callsign, frequency, facility, visual range) and
  ``transceivers-data.json`` (where each controller's transceivers are). Refreshed by VATSIM every 15 s.
- IVAO: ``api.ivao.aero/v2/tracker/whazzup`` (callsign, frequency, position, where they are).

``OnlineStore.get()`` answers at once with the last list fetched, and fetches again in the background once it's a
minute old. The ATC never reaches the network: the list goes to it as ``OnlineAtc`` events (``atc_core.online``).
"""

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from localtc.atc_core.online import airport_field, kind_of, range_of
from localtc.ops.fetch import http_json
from localtc.sim_api import OnlineController

log = logging.getLogger(__name__)

VATSIM_DATA = "https://data.vatsim.net/v3/vatsim-data.json"
VATSIM_TRANSCEIVERS = "https://data.vatsim.net/v3/transceivers-data.json"
IVAO_WHAZZUP = "https://api.ivao.aero/v2/tracker/whazzup"
REFRESH_S = 60.0  # the feeds asked once a minute
RETRY_S = 60.0  # ... and a minute after one failed
VATSIM_FACILITY = {1: "FSS", 2: "DEL", 3: "GND", 4: "TWR", 5: "APP", 6: "CTR"}
UNUSED_MHZ = 199.998  # VATSIM's "no frequency" (observers, a controller not yet primed)
AIRPORT_KINDS = ("DEL", "GND", "TWR", "APP", "ATIS")  # positions at an airport: there, when the feed has no place


def _airport_spot(callsign: str) -> tuple[float, float] | None:
    """Where an airport position is when the feed doesn't say: its airport (LIRF_TWR at Fiumicino; SEA_GND at KSEA)."""
    return airport_field(callsign.upper().split("_", 1)[0])


def parse_vatsim(data: dict, transceivers: list | None) -> list[OnlineController]:
    """VATSIM's controllers with a frequency, where their transceivers are (their airport's place without them)."""
    where: dict[str, tuple[float, float]] = {}
    for entry in transceivers or ():
        spots = [(t.get("latDeg"), t.get("lonDeg")) for t in entry.get("transceivers") or ()]
        spots = [(lat, lon) for lat, lon in spots if isinstance(lat, int | float) and isinstance(lon, int | float)]
        if spots and entry.get("callsign"):
            where[entry["callsign"].upper()] = (sum(s[0] for s in spots) / len(spots), sum(s[1] for s in spots) / len(spots))
    out = []
    for c in [*(data.get("controllers") or ()), *(data.get("atis") or ())]:
        callsign = str(c.get("callsign") or "").upper()
        try:
            mhz = float(c.get("frequency") or 0)
        except ValueError:
            continue
        kind = "ATIS" if callsign.endswith("_ATIS") else VATSIM_FACILITY.get(c.get("facility")) or kind_of(callsign)
        if not kind or not 118.0 <= mhz < 137.0 or abs(mhz - UNUSED_MHZ) < 0.001:
            continue
        spot = where.get(callsign) or (_airport_spot(callsign) if kind in AIRPORT_KINDS else None)
        if spot is None:
            continue
        out.append(OnlineController(callsign=callsign, frequency_mhz=round(mhz, 3), kind=kind, lat=spot[0], lon=spot[1],
                                    range_nm=range_of(kind, float(c.get("visual_range") or 0))))
    return out


def parse_ivao(data: dict) -> list[OnlineController]:
    """IVAO's controllers with a frequency and a place."""
    out = []
    for c in ((data.get("clients") or {}).get("atcs")) or ():
        callsign = str(c.get("callsign") or "").upper()
        session = c.get("atcSession") or {}
        track = c.get("lastTrack") or {}
        kind = str(session.get("position") or "").upper()
        if kind == "DEP":
            kind = "APP"
        elif kind not in ("DEL", "GND", "TWR", "APP", "CTR", "FSS", "ATIS"):
            kind = kind_of(callsign)
        try:
            mhz = float(session.get("frequency") or 0)
        except (TypeError, ValueError):
            continue
        if not kind or not 118.0 <= mhz < 137.0:
            continue
        try:
            lat, lon = float(track["latitude"]), float(track["longitude"])
        except (KeyError, TypeError, ValueError):
            spot = _airport_spot(callsign) if kind in AIRPORT_KINDS else None
            if spot is None:
                continue
            lat, lon = spot
        out.append(OnlineController(callsign=callsign, frequency_mhz=round(mhz, 3), kind=kind, lat=lat, lon=lon,
                                    range_nm=range_of(kind)))
    return out


def fetch(network: str, getter: Callable[[str], Any] = http_json) -> list[OnlineController]:
    """The network's controllers now (blocking)."""
    if network == "vatsim":
        data = getter(VATSIM_DATA)
        try:
            transceivers = getter(VATSIM_TRANSCEIVERS)
        except Exception as exc:  # noqa: BLE001 - the airports' places still do for most
            log.info("VATSIM transceivers not fetched: %s", exc)
            transceivers = None
        return parse_vatsim(data or {}, transceivers)
    if network == "ivao":
        return parse_ivao(getter(IVAO_WHAZZUP) or {})
    raise ValueError(f"no such network: {network}")


class OnlineStore:
    """The last list of a network's controllers, fetched again in the background each minute."""

    def __init__(self, network: str, *, fetcher: Callable[[str], list[OnlineController]] = fetch,
                 background: bool = True, clock: Callable[[], float] = time.monotonic) -> None:
        self.network = network
        self.fetcher = fetcher
        self.background = background
        self.clock = clock
        self.refresh_s = REFRESH_S
        self._held: tuple[OnlineController, ...] | None = None
        self._got: float | None = None
        self._failed: float | None = None
        self.error = ""
        self._fetching = False
        self._lock = threading.Lock()

    def get(self) -> tuple[tuple[OnlineController, ...] | None, str]:
        """(the controllers last fetched, None before the first answer; why the newest fetch failed, "" fine)."""
        now = self.clock()
        with self._lock:
            due = (self._got is None or now - self._got >= self.refresh_s) \
                and (self._failed is None or now - self._failed >= RETRY_S) and not self._fetching
            if due:
                self._fetching = True
        if due:
            if self.background:
                threading.Thread(target=self._load, name=f"online-{self.network}", daemon=True).start()
            else:
                self._load()
        with self._lock:
            return self._held, self.error

    def _load(self) -> None:
        try:
            found = tuple(self.fetcher(self.network))
        except Exception as exc:  # noqa: BLE001 - offline, or the network's feed down: the last list stands
            log.info("%s controllers not fetched: %s", self.network, exc)
            with self._lock:
                self._failed, self.error, self._fetching = self.clock(), str(exc) or type(exc).__name__, False
            return
        with self._lock:
            self._held, self._got, self._failed, self.error, self._fetching = found, self.clock(), None, "", False
        log.info("%s: %d controllers online", self.network, len(found))
