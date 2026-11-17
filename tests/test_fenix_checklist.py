"""The Fenix A320 family's normal checklist read against its own switches (crew/checklist_data/fenix_a32x.toml): an item
already done is said done from the aircraft, one still to do is said and waited for, an item the captain sets is asked
with what the switches show."""

from localtc.crew import checklist_run as run
from localtc.crew.actions import Cockpit
from localtc.crew.checklist_db import Library, bundled
from localtc.crew.pm import PilotMonitoring
from localtc.crew.profiles import for_aircraft
from localtc.sim_api import AircraftIdentity, AircraftVars, IntercomHeard, PhaseChanged, SetSimVar, WatchVars

from tests.test_crew_monitor import PROFILES, FakeEngine, fly, own, said, systems

TITLE = "FenixA320 IAE"


def fenix():
    pm = PilotMonitoring(FakeEngine(), profiles=PROFILES, checklists=Library())
    out = pm.observe(AircraftIdentity(t=0.0, title=TITLE, atc_model="A320"))
    return pm, out


def test_the_fenix_has_its_own_checklist_and_the_others_the_airbus_one():
    library = Library()
    assert library.for_aircraft(TITLE, "A320").name == "Fenix A320 family"
    assert library.for_aircraft("Airbus A320neo", "A20N").name == "Airbus A320 family"
    fnx = next(s for s in bundled() if s.name == "Fenix A320 family")
    assert [c.phase for c in fnx.checklists] == ["cockpit_prep", "before_start", "after_start", "taxi", "line_up",
                                                  "after_takeoff", "approach", "landing", "after_landing", "parking",
                                                  "securing"]
    assert "L:S_OH_NAV_IR3_MODE" in fnx.variables() and "L:S_OH_FUEL_RIGHT_2" in fnx.variables()


def test_its_switches_are_watched_and_its_flaps_read_from_the_lever():
    pm, out = fenix()
    watch = [o for o in out if isinstance(o, WatchVars)][-1].names
    assert {"L:S_FC_FLAPS", "L:S_MIP_PARKING_BRAKE", "L:S_OH_NAV_IR1_MODE", "L:S_XPDR_MODE"} <= set(watch)
    pm.observe(systems(0.5, flaps_positions=8))
    pm.observe(own(1, flaps_index=0))
    pm.observe(AircraftVars(t=1.5, values={"L:S_FC_FLAPS": 2.0}))
    assert pm.cockpit.flaps_known and pm.cockpit.flaps_index == 2


def test_an_item_done_is_said_done_and_one_to_do_waited_for():
    pm, _ = fenix()
    pm.observe(PhaseChanged(t=0.5, previous="PARKED", phase="PARKED"))
    pm.observe(systems(0.6))
    pm.observe(own(1, parking_brake=False))  # the sim's brake: not the Fenix's
    pm.observe(AircraftVars(t=1.5, values={"L:S_MIP_PARKING_BRAKE": 1.0, "L:S_OH_EXT_LT_BEACON": 0.0}))
    out = pm.observe(IntercomHeard(t=2, text="before start checklist"))
    assert " ".join(said(out)) == "Before start checklist. Parking brake, set. Takeoff speeds and thrust?"
    pm.observe(IntercomHeard(t=3, text="checked"))
    out = pm.observe(IntercomHeard(t=4, text="closed both"))
    assert said(out) == ["Beacon? Shows off, still to do."]
    pm.observe(AircraftVars(t=5, values={"L:S_OH_EXT_LT_BEACON": 1.0}))
    out = fly(pm, [own(6 + i, parking_brake=False) for i in range(5)])
    assert " ".join(said(out)) == "Beacon, on. Before start checklist complete."


def test_said_done_at_once_when_the_captain_answers_with_it_set():
    pm, _ = fenix()
    pm.observe(PhaseChanged(t=0.5, previous="PARKED", phase="PARKED"))
    pm.observe(systems(0.6))
    pm.observe(own(1))
    pm.observe(AircraftVars(t=1.5, values={"L:S_MIP_PARKING_BRAKE": 0.0, "L:S_OH_EXT_LT_BEACON": 1.0}))
    out = pm.observe(IntercomHeard(t=2, text="before start checklist"))
    assert " ".join(said(out)) == "Before start checklist. Parking brake? Shows released, still to do."
    pm.observe(AircraftVars(t=2.5, values={"L:S_MIP_PARKING_BRAKE": 1.0}))
    out = pm.observe(IntercomHeard(t=3, text="set"))
    assert " ".join(said(out)) == "Parking brake, set. Takeoff speeds and thrust?"


def test_the_captains_setting_is_asked_with_what_it_shows_and_tcas_set_by_the_copilot():
    pm, _ = fenix()
    pm.observe(PhaseChanged(t=0.5, previous="TAXI_OUT", phase="RUNWAY_HOLD"))
    pm.observe(systems(0.6))
    pm.observe(own(1))
    pm.observe(AircraftVars(t=1.5, values={"L:S_XPDR_MODE": 0.0, "L:S_OH_PNEUMATIC_PACK_1": 1.0,
                                           "L:S_OH_PNEUMATIC_PACK_2": 0.0}))
    pm.observe(IntercomHeard(t=2, text="line up checklist"))
    out = pm.observe(IntercomHeard(t=3, text="one six right, both"))
    words = " ".join(said(out))
    assert words == "TCAS, TA or TA/RA, set. Packs 1 and 2? Showing 1 on, 2 off."
    assert [o for o in out if isinstance(o, SetSimVar) and o.name == "L:S_XPDR_MODE" and o.value == 2]


def test_the_switches_in_words():
    c = Cockpit(profile=for_aircraft(PROFILES, TITLE, "A320"), own=own(0), systems=systems(0))
    fnx = next(s for s in bundled() if s.name == "Fenix A320 family")
    prep, taxi, park = fnx.get("cockpit_prep"), fnx.get("taxi"), fnx.get("parking")
    adirs = next(ln for ln in prep.lines if ln.challenge == "ADIRS")
    autobrake = next(ln for ln in taxi.lines if ln.challenge == "AUTO BRK")
    pumps = next(ln for ln in park.lines if ln.challenge == "FUEL PUMPS")
    assert run.seen(adirs, c, {}) is None  # not read yet: the captain's
    c.vars.update({"L:S_OH_NAV_IR1_MODE": 1.0, "L:S_OH_NAV_IR2_MODE": 1.0, "L:S_OH_NAV_IR3_MODE": 0.0})
    assert run.seen(adirs, c, {}) == ("1 nav, 2 nav, 3 off", False)
    c.vars["L:S_OH_NAV_IR3_MODE"] = 1.0
    assert run.seen(adirs, c, {}) == ("nav", True)
    c.vars.update({"L:I_MIP_AUTOBRAKE_LO_L": 0.0, "L:I_MIP_AUTOBRAKE_MED_L": 1.0, "L:I_MIP_AUTOBRAKE_MAX_L": 0.0})
    assert run.seen(autobrake, c, {}) == ("MED", False)
    c.vars.update({f"L:S_OH_FUEL_{s}_{n}": 0.0 for s in ("LEFT", "CENTER", "RIGHT") for n in (1, 2)})
    assert run.seen(pumps, c, {}) == ("off", True)
    c.vars["L:S_OH_FUEL_CENTER_2"] = 1.0
    assert run.seen(pumps, c, {}) == ("some still on", False)
