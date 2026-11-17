"""Fixes found flying LIMC to LFPG: the descent with a level to stop at (ICAO), the STAR's floor from its published
restrictions, the arrival said as an arrival, a level reported answered, the right controller on a shared frequency,
and a direct only to a fix that exists."""

from types import SimpleNamespace

import msgspec
from test_engine import parked_engine

from localtc.atc_core import region as regions
from localtc.atc_core.engine import _known_fix
from localtc.atc_core.facilities import Facility
from localtc.atc_core.phraseology import speech
from localtc.sim_api import ArrivalData, ArrivalLeg


def test_a_misheard_fix_is_the_routes_fix_it_sounds_like_and_an_unknown_one_none():
    known = {"NOBKE", "AOSTA", "MC521", "URELO"}
    assert _known_fix("Knob Itarrow", known) == "NOBKE"
    assert _known_fix("nobke", known) == "NOBKE"
    assert _known_fix("NOBKE Itarrow 1128", known) == "NOBKE"
    assert _known_fix("Kilimanjaro", known) is None


def test_the_fixes_a_flight_knows_are_its_routes_without_the_plans_markers():
    engine, own = parked_engine()
    engine.cfg.route = (SimpleNamespace(ident="NOBKE"), SimpleNamespace(ident="AOSTA"))
    assert engine._known_fixes() == {"NOBKE", "AOSTA"}


def test_the_stars_floor_is_its_lowest_published_level_on_the_landing_runways_way_in():
    engine, own = parked_engine(star="TINI9W", destination="LFPG")
    legs = (ArrivalLeg(fix="FF302", altitude="below", alt1_ft=28000),
            ArrivalLeg(fix="URELO", altitude="below", alt1_ft=12000),
            ArrivalLeg(fix="OKIPA", altitude="between", alt1_ft=11000, alt2_ft=7000),
            ArrivalLeg(fix="LORNI", altitude="at", alt1_ft=4000, transition="RW27R"))  # another runway's
    engine.state.flight.destination = "LFPG"
    engine.handle(ArrivalData(t=1, airport="LFPG", name="TINI9W", legs=legs))
    engine.state.assignments.arrival_runway = "09R"
    assert engine._star_floor() == 11000


def test_europe_says_flight_levels_above_its_own_transition_altitude():
    with regions.speaking(regions.region_for("LFFF")):
        assert speech.altitude_display(11000) == "FL110"
        assert speech.altitude_display(4000) == "4,000 feet"
    with regions.speaking(regions.region_for("KZSE")):
        assert speech.altitude_display(11000) == "11,000"


def test_a_star_said_again_is_an_arrival_not_a_departure():
    engine, own = parked_engine()
    said = engine._fragments(["procedure"], {"procedure": "TINI9W", "approach": "ILS RWY 09R"})
    assert said.display == "via the TINI9W arrival"
    assert engine._fragments(["procedure"], {"procedure": "AOS8Z"}).display == "via the AOS8Z departure"


def test_the_icao_descent_templates_carry_the_level_and_its_readback():
    engine, own = parked_engine()
    library = engine.libraries["icao"]
    for iid in ("center.descend_star", "approach.cleared_star_descend"):
        assert "altitude" in library.get(iid).readback.required


def test_a_level_reported_to_radar_is_answered_never_left_in_silence():
    engine, own = parked_engine()
    radar = Facility("approach", "De Gaulle Approach", 118.155, "LFPG")
    engine._checked_in.add(radar.station)
    up = msgspec.structs.replace(own, on_ground=False, alt_indicated_ft=15000, alt_agl_ft=14600)
    engine.state.aircraft = up
    engine.state.assignments.altitude_ft = 4000
    report = SimpleNamespace(intent="acknowledge", values={"altitude": "15000"})
    assert engine._level_report(report, radar, 10.0, up)
    assert engine._scheduled[-1].instruction_id == "common.descend"  # the 4,000 said again
    engine._scheduled.clear()
    engine.state.assignments.altitude_ft = 15000
    assert engine._level_report(report, radar, 20.0, up)
    assert engine._scheduled[-1].instruction_id == "common.roger"
    engine._scheduled.clear()
    thanks = SimpleNamespace(intent="acknowledge", values={})
    assert not engine._level_report(thanks, radar, 30.0, up)  # a thank-you: nothing to answer


def test_on_a_shared_frequency_the_controller_that_just_sent_the_flight_on_answers():
    engine, own = parked_engine()
    clearance = Facility("clearance", "Malpensa Clearance", 120.9, engine.state.flight.origin)
    tower = Facility("tower", "Malpensa Planning Tower", 120.9, engine.state.flight.origin)
    engine.facilities = [clearance, tower, *engine.facilities]
    assert engine._facility_for(120.9) is clearance  # parked: the airport's own order (its clearance first here)
    up = msgspec.structs.replace(own, on_ground=False, alt_indicated_ft=1500, alt_agl_ft=900)
    engine.state.aircraft = up
    engine.state.phase = "DEPARTURE"
    assert engine._facility_for(120.9) is tower  # in the air: the tower
    engine._last_handoff = (5.0, tower)
    assert engine._facility_for(120.9) is tower


def test_numbered_pieces_of_one_taxiway_in_a_row_are_said_as_that_taxiway():
    from localtc.atc_core.airport.taxi_route import _simplified

    assert _simplified(["G1", "G2", "G3"]) == ["G"]
    assert _simplified(["G", "G1", "G2", "B"]) == ["G", "B"]
    assert _simplified(["H1", "H2", "GE", "G1", "G2"]) == ["H", "GE", "G"]
    assert _simplified(["A", "A1"]) == ["A", "A1"]  # turning off A onto A1: said
    assert _simplified(["C3", "C", "C1"]) == ["C3", "C", "C1"]  # off the stand on C3, along C, onto C1
    assert _simplified(["B", "E", "C", "H", "GE"]) == ["B", "E", "C", "H", "GE"]


def test_the_descent_given_ahead_of_the_top_of_descent_is_when_ready():
    engine, own = parked_engine()
    library = engine.libraries["icao"]
    assert "when ready" in library.get("center.descend_star_pd").text[0]
    assert library.get("center.descend_star_pd").readback.required == library.get("center.descend_star").readback.required


def test_the_ils_clearance_in_europe_says_the_tower_to_call_when_established():
    engine, own = parked_engine()
    library = engine.libraries["icao"]
    assert library.get("approach.cleared").text[0].endswith("when established.")
    assert "when established" in library.get("approach.cleared_star_descend_tower").text[0]
    assert "frequency" in library.get("approach.cleared_star_descend_tower").readback.required


def test_an_emergency_is_cancelled_by_voice_and_never_taken_for_another():
    from test_engine import say, transmissions

    from localtc.atc_core.engine import CANCEL_EMERGENCY

    for said in ("Negative, negative. Cancel the emergency.", "cancelling mayday", "We're no longer an emergency",
                 "cancel pan pan"):
        assert CANCEL_EMERGENCY.search(said), said
    for said in ("Copy information.", "Mayday mayday, engine fire", "emergency equipment standing by?"):
        assert not CANCEL_EMERGENCY.search(said), said
    engine, own = parked_engine()
    say(engine, own, 5, "Mayday mayday, Skyhawk 172LT, engine fire")
    assert "emergency" in engine.state.flags
    (cancelled,) = transmissions(say(engine, own, 20, "Negative, negative, cancel the emergency, 172LT"))
    assert cancelled.instruction_id == "common.emergency_cancelled" and "emergency" not in engine.state.flags


def test_a_callsign_no_airline_list_knows_is_heard_spelled_or_typed():
    from localtc.atc_core.readback.callsign_check import judge
    from localtc.atc_core.readback.normalize import normalize
    from localtc.atc_core.session import Callsign

    ours = Callsign.named("VND1128")  # a virtual airline's: no telephony LocalTC knows
    for said in ("Genoa Hello, Victor November Delta 1128. Request IFR as filed.", "VND1128", "vnd 1128",
                 "Copy the wind and caution, cleared for takeoff runway 10, Victor November Delta 1128."):
        assert judge(normalize(said), ours) == "ours", said
    assert judge(normalize("Victor November Delta 1123"), ours) == "other"


def test_a_callsign_said_as_the_pilot_wrote_it_is_said_and_heard_so():
    from localtc.atc_core.readback.callsign_check import judge
    from localtc.atc_core.readback.normalize import normalize
    from localtc.atc_core.session import Callsign

    ours = Callsign.named("VND1128").said_as("ita arrow 1128")
    assert speech.callsign(ours) == "ita arrow one one two eight"  # its number digit by digit
    assert speech.callsign_display(ours) == "Ita Arrow 1128"
    for said in ("Genoa ground, ita arrow 1128, request taxi", "ita arrow one one two eight"):
        assert judge(normalize(said), ours) == "ours", said
    assert judge(normalize("ita arrow 1123"), ours) == "close"
    assert Callsign.named("VND1128").said_as("  ") == Callsign.named("VND1128")


def test_the_pilots_words_for_the_callsign_change_in_flight_and_go_back():
    engine, own = parked_engine()
    ident = engine.state.flight.callsign.ident
    before = speech.callsign(engine.state.flight.callsign)
    engine.set_spoken_callsigns({ident: "Speedbird one two three"})
    assert speech.callsign(engine.state.flight.callsign) == "Speedbird one two three"
    engine.set_spoken_callsigns({ident: ""})
    assert speech.callsign(engine.state.flight.callsign) == before


def test_a_crossing_restriction_said_again_is_a_crossing_never_a_direct():
    from localtc.atc_core.engine import _said_again

    assert _said_again(["fix", "altitude"], "center.descend_cross") == ["cross_fix_altitude"]
    assert _said_again(["fix"], "center.descend_cross") == ["cross_fix"]
    assert _said_again(["fix"], "common.hold") == ["hold_fix"]
    assert _said_again(["fix"], "common.direct") == ["fix"]
    engine, own = parked_engine()
    said = engine._fragments(_said_again(["fix", "altitude"], "center.descend_cross"), {"fix": "NORNI", "altitude": 8000})
    assert said.display.startswith("cross NORNI at")


def test_a_fix_heard_with_an_article_or_as_it_sounds_is_the_fix_given():
    from localtc.atc_core.readback.extract import fixes
    from localtc.atc_core.readback.normalize import normalize

    assert fixes(normalize("descend at pilot's discretion and cross a normie at 8,000 feet"), "NORNI") == ["NORNI"]
    assert fixes(normalize("cross normie at 8000"), "NORNI") == ["NORNI"]
    assert fixes(normalize("direct a teg"), "ANTEG") == ["ANTEG"]
    assert fixes(normalize("cross nanny at 8000"), "NORNI") != ["NORNI"]


def test_the_speed_given_said_back_bare_is_read_back():
    from localtc.atc_core.readback.extract import speeds
    from localtc.atc_core.readback.normalize import normalize

    for said in ("And then 180 until final, Victor November Delta.", "180.", "mantain 180 untin 4 nm final"):
        assert speeds(normalize(said), 180) == [180], said
    assert speeds(normalize("heading 180"), 180) == []
    assert speeds(normalize("180"), None) == []


def test_a_speed_until_five_mile_final_is_never_said_again_at_it_or_after_another():
    engine, own = parked_engine()
    engine.state.issued["approach.speed_until"] = SimpleNamespace(slots={"speed": 180})
    engine.state.assignments.speed_kt = 180
    engine.tracker.context = SimpleNamespace(final=SimpleNamespace(distance_nm=9.0))
    assert not engine._overtaken("approach.speed_until")
    engine.tracker.context = SimpleNamespace(final=SimpleNamespace(distance_nm=4.0))
    assert engine._overtaken("approach.speed_until")
    engine.tracker.context = SimpleNamespace(final=SimpleNamespace(distance_nm=9.0))
    engine.state.assignments.speed_kt = 160
    assert engine._overtaken("approach.speed_until")


def test_a_taxi_route_typed_together_or_with_a_name_doubled_is_read_back():
    from localtc.atc_core.readback.extract import ELEMENTS, taxi_route_matches, without_callsign
    from localtc.atc_core.readback.normalize import normalize
    from localtc.atc_core.session import Callsign

    route, ours = ("D", "G", "O"), Callsign.named("VND1128")
    for said in ("dgo", "Taxi to general aviation via D, G, O, O, hold short 21R, Victor November Delta."):
        heard = ELEMENTS["taxi_route"](without_callsign(normalize(said), ours), route)
        assert heard and taxi_route_matches(heard[0], route), said
    assert not taxi_route_matches(("D", "G", "C"), route)


def test_a_descent_at_pilots_discretion_is_marked_until_another_level_is_given():
    engine, own = parked_engine()
    engine.discretion_alt = 8000
    engine._assign(altitude_ft=6000)
    assert engine.discretion_alt is None


# --- Malpensa to Charles de Gaulle on IVAO: what speech-to-text made of the readbacks ------------------------------


def _read(text, instruction_id, expected, callsign="ITY1128"):
    from localtc.atc_core.readback.interpreter import GrammarInterpreter, InterpretContext, PendingReadback
    from localtc.atc_core.session import Callsign

    pending = PendingReadback(instruction_id, "center", expected, tuple(expected))
    return GrammarInterpreter().interpret(text, pending, InterpretContext(callsign=Callsign.named(callsign)))


def test_frequencies_with_two_written_to_or_a_comma_before_decimal_are_heard_whole():
    from localtc.atc_core.readback.extract import frequencies
    from localtc.atc_core.readback.normalize import normalize

    assert frequencies(normalize("Contact Switzerland Radar 1, 3, 5 decimal 6 to 5 Itarrow 12-day.")) == [135.625]
    assert frequencies(normalize("contact Malpensa Ground 1 to 1 decimal 8 to 5 day")) == [121.825]
    assert frequencies(normalize("Contact ground, 1, 2, 1, decimal 8, 2, 5, Itarrow 1128, good day.")) == [121.825]
    assert [str(t) for t in normalize("climb to 5000")] == ["climb", "to", "<5000>"]  # "to" stays a word elsewhere
    assert [str(t) for t in normalize("2 to 5 miles")] == ["<2>", "to", "<5>", "miles"]


def test_a_flight_level_read_back_without_flight_or_misheard_is_read_back():
    for said in ("Climb for level 340 Itarrow 1128.", "Climb to level 340 Itarrow 1128.", "Continue 340 Itarrow 1128.",
                 "We're going to climb to level 340 Itarrow 1128."):
        assert _read(said, "common.climb", {"altitude": 34000}).status == "correct", said
    assert _read("Climb to flow 170. Itarrow 1128.", "common.climb", {"altitude": 17000}).status == "correct"
    assert _read("Mante infra level 0, 9R, 0. Itarrow 1128.", "common.read_back", {"altitude": 9000}).status == "correct"
    wrong = _read("Climb to flight level 240 Itarrow 1128.", "common.climb", {"altitude": 34000})
    assert wrong.status == "incorrect" and wrong.mismatched == {"altitude": 24000}


def test_gulf_is_golf_and_a_fix_said_as_it_sounds_is_the_fix():
    taxi = _read("Holding point Gulf Echo runway 35L via M, C, H, Gulf Echo, Itarrow 1128.", "ground.taxi_out_at",
                 {"taxi_route": ("M", "C", "H", "GE")})
    assert taxi.status == "correct"
    assert _read("Direct dog of Itarrow 1128.", "common.direct", {"fix": "DOGUB"}).status == "correct"
    assert _read("Direct dogma Itarrow 1128.", "common.direct", {"fix": "DOGUB"}).status == "incorrect"


def test_ifr_as_filed_heard_as_ifrs_file_with_a_hello_is_the_request_and_expect_runway_said_back_is_nothing():
    from localtc.atc_core.readback.interpreter import GrammarInterpreter, InterpretContext
    from localtc.atc_core.session import Callsign

    ctx = InterpretContext(callsign=Callsign.named("ITY1128"))
    first = GrammarInterpreter().interpret("Malpensa Clearance Hello Itarrow 1128 with you. Request IFRS file.", None, ctx)
    assert first.intent == "request_ifr_clearance" and not first.needs_fallback
    for said in ("Expector runway 35L Itarrow 1128.", "Expect runway 35L, Itarrow 1128."):
        assert GrammarInterpreter().interpret(said, None, ctx).intent == "acknowledge", said
    assert GrammarInterpreter().interpret("Request runway 35L", None, ctx).intent == "request_runway"


def test_the_handoff_read_back_on_the_old_frequency_isnt_sent_on_again():
    import re

    from test_engine import say, transmissions

    engine, own = parked_engine(destination="KBFI", cruise_ft=12000)
    (cleared,) = transmissions(say(engine, own, 5, "Paine Clearance, 172LT, IFR to Boeing Field, ready to copy"))
    alt = re.search(r"maintain ([\d,]+)", cleared.text).group(1)
    squawk = re.search(r"squawk (\d{4})", cleared.text).group(1)
    dep = re.search(r"departure frequency ([\d.]+)", cleared.text).group(1)
    (done,) = transmissions(say(engine, own, 30, f"cleared to Boeing Field, maintain {alt}, expect 12,000, departure {dep}, "
                                                 f"squawk {squawk}, 2LT"))
    ground = re.search(r"Ground ([\d.]+)", done.text).group(1)
    assert transmissions(say(engine, own, 50, f"Ground {ground}, 2LT, good day")) == []
    (reminder,) = transmissions(say(engine, own, 70, "request taxi, 2LT"))  # anything else: sent on, as before
    assert "Ground" in reminder.text


def test_no_clearance_alerts_while_on_the_online_networks_controller():
    from localtc.sim_api import AtcAlert, PhaseChanged

    engine, own = parked_engine(online_network="ivao")
    engine.state.flight.origin = engine.state.flight.origin or "KPAE"
    engine.online_tuned = SimpleNamespace(callsign="KPAE_GND", frequency_mhz=own.com1_mhz)
    engine.state.phase = "TAXI_OUT"
    out = engine._on_phase_change(PhaseChanged(t=5, previous="PARKED", phase="TAXI_OUT"), own)
    assert not [o for o in out if isinstance(o, AtcAlert) and o.kind.endswith("without_clearance")]
    engine.online_tuned = None
    out = engine._on_phase_change(PhaseChanged(t=6, previous="PARKED", phase="TAXI_OUT"), own)
    assert [o.kind for o in out if isinstance(o, AtcAlert)] == ["taxi_without_clearance"]


def test_a_checklist_asked_for_unheard_is_the_one_just_offered():
    from test_crew_monitor import crew, said, systems
    from test_crew_monitor import own as cockpit

    from localtc.crew.monitor import Offer
    from localtc.sim_api import IntercomHeard, PhaseChanged

    pm = crew()
    pm.observe(PhaseChanged(t=0.5, previous="PUSHBACK", phase="PARKED"))
    pm.observe(systems(0.6))
    pm.observe(cockpit(1, parking_brake=True))
    pm.monitor.f.checklists.update({"cockpit_prep", "before_start"})
    pm.monitor.offer = Offer("checklist", "after_start", 60.0)
    assert said(pm.observe(IntercomHeard(t=2, text="Officer's on checklist.")))[0].startswith("After start checklist.")


def test_an_ivao_approach_with_a_sector_in_its_callsign_works_that_sectors_outline_round_its_airport():
    """LIMC_ANE_APP, placed by IVAO near Piacenza: Milan's ANE sector (round Malpensa), not the one under that point;
    tuned to it from the west, short of the line, the flight is with them."""
    from localtc.atc_core import online as online_atc
    from localtc.sim_api import OnlineController

    milan = OnlineController(callsign="LIMC_ANE_APP", frequency_mhz=126.75, kind="APP", lat=44.91672, lon=9.7126,
                             range_nm=40.0, name="")
    area = online_atc.area_of(milan)
    assert area is not None and "LIMM_ANE" in area.airports and area.contains(45.63, 8.72)
    near_turin = online_atc.point(45.51, 7.46, ground=False, alt_ft=23700, agl_ft=23000)
    assert online_atc._reaches(milan, near_turin)
