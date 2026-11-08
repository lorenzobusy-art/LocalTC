"""Online ATC (VATSIM, IVAO) beside LocalTC's: which of the networks' controllers cover the aircraft, which one the
flight should be with for its phase, and whether COM1 is on one of theirs (then LocalTC's ATC stays quiet).

Pure functions of the ``OnlineAtc`` controllers and the aircraft: nothing is fetched here (``localtc.online`` does
that, outside the ATC), so a recording replays the same.

Coverage is a circle around each controller: the range the network gives (VATSIM's visual range), held to what that
kind of position works (a ground controller doesn't reach 100 nm), or one for its kind where the network gives none
(IVAO). Delivery and ground only for an aircraft on the ground, tower low near its field, approach below FL245.
"""

from collections.abc import Iterable

from localtc.sim_api import OnlineController, OwnshipState
from localtc.sim_api.geo import haversine_nm as distance_nm

NETWORKS = {"vatsim": "VATSIM", "ivao": "IVAO"}

# The range each kind of position gets where the network gives none, and the most it's held to where it does.
DEFAULT_RANGE_NM = {"DEL": 10.0, "GND": 10.0, "TWR": 30.0, "APP": 80.0, "CTR": 300.0, "FSS": 600.0, "ATIS": 60.0}
MAX_RANGE_NM = {"DEL": 15.0, "GND": 15.0, "TWR": 40.0, "APP": 150.0, "CTR": 1000.0, "FSS": 1500.0, "ATIS": 100.0}
# (An ATIS is never the one to call, but COM1 on it is listening to the network: LocalTC stays quiet.)
TOWER_AGL_FT = 5000.0  # a tower works the aircraft below this (above its field)
APPROACH_MAX_FT = 24500.0  # an approach (or departure) controller below FL245
TUNED_SLACK = 1.5  # COM1 on a controller's frequency counts as with them out to this times their range
SAME_MHZ = 0.004  # 121.800 and 121.805 are one frequency (8.33 kHz spacing)
RELEVANT_NM = 400.0  # the controllers kept around the aircraft and its route's points (beyond their own range)

# Who the flight should be with in each phase, first choice first (a missing position's airspace is its parent's:
# no delivery, ground does it; no ground, tower ...).
BY_PHASE = {
    "PARKED": ("DEL", "GND", "TWR", "APP", "CTR", "FSS"),
    "PUSHBACK": ("GND", "DEL", "TWR", "APP", "CTR", "FSS"),
    "TAXI_OUT": ("GND", "TWR", "APP", "CTR", "FSS"),
    "RUNWAY_HOLD": ("TWR", "GND", "APP", "CTR", "FSS"),
    "TAKEOFF": ("TWR", "APP", "CTR", "FSS"),
    "DEPARTURE": ("APP", "CTR", "TWR", "FSS"),
    "CRUISE": ("CTR", "APP", "FSS"),
    "ARRIVAL": ("APP", "CTR", "FSS"),
    "APPROACH": ("APP", "TWR", "CTR", "FSS"),
    "LANDING": ("TWR", "APP", "CTR", "FSS"),
    "TAXI_IN": ("GND", "TWR", "APP", "CTR", "FSS"),
}
GROUND_KINDS = ("DEL", "GND")


def network_name(network: str) -> str:
    """"VATSIM" for "vatsim"."""
    return NETWORKS.get(network, network.upper())


def kind_of(callsign: str) -> str:
    """The position's kind from its callsign's last part (LIRF_TWR: TWR; DEP is approach's), or "" (an observer,
    ATIS)."""
    last = callsign.upper().rsplit("_", 1)[-1]
    if last == "DEP":
        return "APP"
    return last if last in DEFAULT_RANGE_NM else ""


def range_of(kind: str, given_nm: float = 0.0) -> float:
    """How far a position of ``kind`` reaches: ``given_nm`` (the network's) held to its kind's most, else its kind's."""
    if given_nm and given_nm > 0:
        return min(given_nm, MAX_RANGE_NM.get(kind, given_nm))
    return DEFAULT_RANGE_NM.get(kind, 50.0)


def same_mhz(a: float, b: float) -> bool:
    return abs(a - b) < SAME_MHZ


def covers(c: OnlineController, own: OwnshipState) -> bool:
    """``c`` works the aircraft where it is: in their range, and the kind of position for its height."""
    if distance_nm(own.lat, own.lon, c.lat, c.lon) > c.range_nm:
        return False
    if c.kind in GROUND_KINDS:
        return own.on_ground
    if c.kind == "TWR":
        return own.on_ground or own.alt_agl_ft < TOWER_AGL_FT
    if c.kind == "APP":
        return own.alt_indicated_ft < APPROACH_MAX_FT
    return True


def covering(controllers: Iterable[OnlineController], own: OwnshipState) -> list[OnlineController]:
    return [c for c in controllers if covers(c, own)]


def best(controllers: Iterable[OnlineController], own: OwnshipState, phase: str) -> OnlineController | None:
    """The controller the flight should be with now: of those covering it, the first kind its phase calls for, the
    nearest of that kind (an airport's own tower, not the next field's)."""
    order = BY_PHASE.get(phase, BY_PHASE["CRUISE"])
    here = [c for c in covering(controllers, own) if c.kind in order]
    if not here:
        return None
    return min(here, key=lambda c: (order.index(c.kind), distance_nm(own.lat, own.lon, c.lat, c.lon), c.callsign))


def tuned_to(controllers: Iterable[OnlineController], own: OwnshipState) -> OnlineController | None:
    """The online controller COM1 (the radio transmitting) is on, within reach of them; None: none of theirs."""
    mhz = own.tx_mhz
    near = [c for c in controllers if same_mhz(c.frequency_mhz, mhz)
            and distance_nm(own.lat, own.lon, c.lat, c.lon) <= c.range_nm * TUNED_SLACK]
    return min(near, key=lambda c: distance_nm(own.lat, own.lon, c.lat, c.lon)) if near else None


def relevant(controllers: Iterable[OnlineController], points: Iterable[tuple[float, float]],
             nm: float = RELEVANT_NM) -> list[OnlineController]:
    """The controllers that matter to the flight: within their range plus ``nm`` of any of ``points`` (the aircraft,
    the airports, the route's fixes)."""
    points = list(points)
    return [c for c in controllers
            if any(distance_nm(lat, lon, c.lat, c.lon) <= c.range_nm + nm for lat, lon in points)]


def frequency_text(mhz: float) -> str:
    """121.8 as "121.800"."""
    return f"{mhz:.3f}"
