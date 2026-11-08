"""VATSIM/IVAO beside LocalTC ([atc] online_integration): the networks' feeds read, which controller covers the
aircraft for its phase, the pilot told of them, LocalTC's ATC quiet on their frequency and back off it."""

import asyncio

import msgspec
from helpers.airports import kpae
from test_engine import parked_engine, say, transmissions

from localtc.atc_core import online as online_atc
from localtc.atc_core.service import AtcService
from localtc.bus import EventBus
from localtc.online import OnlineStore, parse_ivao, parse_vatsim
from localtc.sim_api import AtcAlert, OnlineAtc, OnlineController, RadioTuned, Transcript

LAT, LON = kpae().lat, kpae().lon


def ctl(callsign: str, mhz: float, lat: float = LAT, lon: float = LON, kind: str = "") -> OnlineController:
    kind = kind or online_atc.kind_of(callsign)
    return OnlineController(callsign=callsign, frequency_mhz=mhz, kind=kind, lat=lat, lon=lon,
                            range_nm=online_atc.range_of(kind))


def alerts(outputs, kind: str) -> list[AtcAlert]:
    return [o for o in outputs if isinstance(o, AtcAlert) and o.kind == kind]


def test_vatsims_feed_gives_the_controllers_where_their_transceivers_are():
    data = {"controllers": [
        {"callsign": "KPAE_TWR", "frequency": "132.950", "facility": 4, "visual_range": 50},
        {"callsign": "SEA_GND", "frequency": "121.700", "facility": 3, "visual_range": 0},  # no transceivers: KSEA's place
        {"callsign": "JD_OBS", "frequency": "199.998", "facility": 0, "visual_range": 40},  # an observer
        {"callsign": "ZSE_CTR", "frequency": "199.998", "facility": 6, "visual_range": 600},  # not primed yet
    ]}
    transceivers = [{"callsign": "KPAE_TWR", "transceivers": [{"latDeg": 47.9, "lonDeg": -122.28}]}]
    found = {c.callsign: c for c in parse_vatsim(data, transceivers)}
    assert set(found) == {"KPAE_TWR", "SEA_GND"}
    assert found["KPAE_TWR"].kind == "TWR" and found["KPAE_TWR"].range_nm == 40.0  # held to a tower's reach
    assert found["KPAE_TWR"].lat == 47.9
    assert found["SEA_GND"].kind == "GND" and abs(found["SEA_GND"].lat - 47.45) < 0.1


def test_ivaos_feed_gives_the_controllers_where_they_are():
    data = {"clients": {"atcs": [
        {"callsign": "LIRF_TWR", "atcSession": {"frequency": 118.7, "position": "TWR"},
         "lastTrack": {"latitude": 41.8, "longitude": 12.25}},
        {"callsign": "LIRR_CTR", "atcSession": {"frequency": 125.5, "position": "CTR"}, "lastTrack": None},  # nowhere
        {"callsign": "LIRF_DEP", "atcSession": {"frequency": 119.2, "position": "DEP"},
         "lastTrack": {"latitude": 41.8, "longitude": 12.25}},
    ]}}
    found = {c.callsign: c for c in parse_ivao(data)}
    assert set(found) == {"LIRF_TWR", "LIRF_DEP"}
    assert found["LIRF_DEP"].kind == "APP" and found["LIRF_TWR"].range_nm == 30.0


def test_the_controller_for_the_phase_falls_back_to_the_one_above():
    engine, own = parked_engine()
    online = [ctl("KPAE_TWR", 132.95), ctl("KPAE_GND", 121.8), ctl("ZSE_CTR", 124.2, kind="CTR")]
    assert online_atc.best(online, own, "PARKED").callsign == "KPAE_GND"  # no delivery: ground does it
    assert online_atc.best(online, own, "RUNWAY_HOLD").callsign == "KPAE_TWR"
    airborne = msgspec.structs.replace(own, on_ground=False, alt_agl_ft=30000, alt_indicated_ft=31000)
    assert online_atc.best(online, airborne, "CRUISE").callsign == "ZSE_CTR"
    far = msgspec.structs.replace(own, lat=LAT + 3)  # 180 nm away, on the ground: nobody's there
    assert online_atc.best(online[:2], far, "PARKED") is None


def test_the_pilot_is_told_and_localtc_is_quiet_on_their_frequency_then_back():
    engine, own = parked_engine(destination="KBFI", cruise_ft=12000, online_network="vatsim")
    engine.handle(OnlineAtc(t=1, network="vatsim", controllers=(ctl("KPAE_DEL", 128.0),)))
    (told,) = alerts(engine.handle(msgspec.structs.replace(own, t=2)), "online")
    assert told.detail == "VATSIM connection for this airspace: KPAE_DEL, set 128.000 on COM1"
    assert alerts(engine.handle(msgspec.structs.replace(own, t=3)), "online") == []  # once

    on_vatsim = msgspec.structs.replace(own, t=4, com1_mhz=128.0)
    tuned = [o for o in engine.handle(on_vatsim) if isinstance(o, RadioTuned)]
    assert engine.online_muted and tuned[0].station == "KPAE_DEL (VATSIM)"
    assert transmissions(say(engine, on_vatsim, 5, "Paine Clearance, 172LT, IFR to Boeing Field, ready to copy")) == []

    back = engine.handle(msgspec.structs.replace(own, t=20))  # COM1 back on LocalTC's clearance
    assert not engine.online_muted and alerts(back, "online_back")[0].detail == "off KPAE_DEL: LocalTC ATC is back"
    (clearance,) = transmissions(say(engine, own, 21, "Paine Clearance, 172LT, IFR to Boeing Field, ready to copy"))
    assert clearance.instruction_id == "clearance.ifr"


def test_localtc_takes_over_when_the_online_controller_leaves():
    engine, own = parked_engine(online_network="ivao")
    engine.handle(OnlineAtc(t=1, network="ivao", controllers=(ctl("KPAE_GND", 121.8),)))
    engine.handle(msgspec.structs.replace(own, t=2, com1_mhz=121.8))
    assert engine.online_muted
    engine.handle(OnlineAtc(t=60, network="ivao", controllers=()))
    (back,) = alerts(engine.handle(msgspec.structs.replace(own, t=61, com1_mhz=121.8)), "online_back")
    assert back.detail == "KPAE_GND has left IVAO: LocalTC ATC is back" and not engine.online_muted


def test_without_a_network_the_online_controllers_change_nothing():
    engine, own = parked_engine()
    engine.handle(OnlineAtc(t=1, network="vatsim", controllers=(ctl("KPAE_DEL", 128.0),)))
    outputs = engine.handle(msgspec.structs.replace(own, t=2, com1_mhz=128.0))
    assert not engine.online_muted and alerts(outputs, "online") == []


def test_the_service_publishes_the_controllers_around_the_flight():
    engine, own = parked_engine(online_network="vatsim")
    bus = EventBus()
    seen = bus.subscribe(OnlineAtc)
    service = AtcService(engine, bus)
    near, far = ctl("KPAE_TWR", 132.95), ctl("RJTT_TWR", 118.1, lat=35.55, lon=139.78)
    service.online_source = lambda: ((near, far), "")
    service._fetch_online(10.0)
    service._fetch_online(30.0)  # within the minute: not asked again
    service._fetch_online(75.0)  # asked again: the same, not published again

    async def first():
        return await asyncio.wait_for(seen.__anext__(), 1)

    published = asyncio.run(first())
    assert published.controllers == (near,) and published.network == "vatsim"


def test_the_store_fetches_once_a_minute_and_keeps_the_last_list():
    now = [0.0]
    calls = []

    def fetcher(network):
        calls.append(network)
        if len(calls) == 2:
            raise OSError("offline")
        return [ctl("KPAE_TWR", 132.95)]

    store = OnlineStore("vatsim", fetcher=fetcher, background=False, clock=lambda: now[0])
    first, error = store.get()
    assert len(first) == 1 and error == "" and calls == ["vatsim"]
    now[0] = 30.0
    store.get()
    assert len(calls) == 1
    now[0] = 61.0
    kept, error = store.get()
    assert kept == first and error == "offline" and len(calls) == 2


def test_the_copilot_says_the_frequency_and_keeps_off_their_radio():
    from test_crew_monitor import FakeEngine, crew, own, said

    from localtc.copilot import Copilot

    engine = FakeEngine()
    engine.cfg.online_network = "vatsim"
    engine.online_best = ctl("KSEA_DEL", 128.0)
    pm = crew(engine)
    out = pm.observe(AtcAlert(t=1.0, kind="online", detail="VATSIM connection for this airspace"))
    out += [o for t in range(2, 12) for o in pm.observe(own(float(t)))]
    assert said(out) == ["VATSIM connection for this airspace, set 128.0 on COM1."]

    real, _ = parked_engine(online_network="vatsim")
    copilot = Copilot(real, mode="full", delay_s=(1.0, 1.0))
    real.online_tuned = ctl("KPAE_DEL", 128.0)
    assert copilot.due(5.0) == []


def test_com1_on_the_networks_atis_keeps_localtc_quiet_and_is_never_the_one_to_call():
    data = {"controllers": [], "atis": [{"callsign": "KPAE_ATIS", "frequency": "128.650", "facility": 4,
                                         "visual_range": 0}]}
    (atis,) = parse_vatsim(data, [{"callsign": "KPAE_ATIS", "transceivers": [{"latDeg": LAT, "lonDeg": LON}]}])
    assert atis.kind == "ATIS"
    engine, own = parked_engine(online_network="vatsim")
    engine.handle(OnlineAtc(t=1, network="vatsim", controllers=(atis,)))
    outputs = engine.handle(msgspec.structs.replace(own, t=2, com1_mhz=128.65))
    assert engine.online_muted and engine.online_best is None and alerts(outputs, "online") == []


def test_on_the_networks_frequency_the_radio_key_records_and_transcribes_nothing():
    from test_stt import FakeCapture, FakeTranscriber

    from localtc.stt.service import VoiceService

    def press_and_release(online: bool):
        async def main():
            bus = EventBus()
            capture, transcriber = FakeCapture(2.0), FakeTranscriber()
            service = VoiceService(bus, lambda: 1.0, transcriber, capture, tail_s=0.0)
            service.skip_radio = lambda: online
            heard = bus.subscribe(Transcript)
            task = asyncio.create_task(service.run())
            await asyncio.sleep(0)
            service.press()
            await asyncio.sleep(0.01)
            service.release()
            await asyncio.sleep(0.05)
            bus.close()
            await task
            return [e async for e in heard], capture, transcriber

        return asyncio.run(main())

    (heard,), capture, transcriber = press_and_release(online=True)
    assert (heard.text, heard.source) == ("", "cancelled") and capture.began == 0 and transcriber.calls == []
    (heard,), capture, transcriber = press_and_release(online=False)
    assert heard.text and capture.began == 1


def test_a_centre_works_its_fir_and_an_approach_its_area_as_localtc_draws_them():
    from localtc.atc_core.airspace import Airspace

    sky = Airspace.load()
    engine, own = parked_engine()
    # ZSE_CTR, said to sit far away in Oregon: Seattle Center's outline still covers Paine, as LocalTC's centre does.
    seattle = ctl("ZSE_CTR", 124.2, lat=44.0, lon=-121.0, kind="CTR")
    assert online_atc.area_of(seattle).id == "KZSE" and online_atc.covers(seattle, own)
    # LON_N_CTR has no outline of its name: the one it sits in.
    london = ctl("LON_N_CTR", 133.7, lat=52.0, lon=-0.5, kind="CTR")
    assert online_atc.area_of(london) is sky.center_at(52.0, -0.5)
    # Seattle Approach: the area named for SEA, which works Paine too; not up at FL300.
    approach = ctl("SEA_APP", 119.2, lat=47.45, lon=-122.31)
    assert online_atc.area_of(approach).serves("KSEA") and online_atc.covers(approach, own)
    high = msgspec.structs.replace(own, on_ground=False, alt_indicated_ft=30000, alt_agl_ft=29400)
    assert not online_atc.covers(approach, high) and online_atc.best([seattle, approach], high, "CRUISE") is seattle
    # Out of Seattle Center's outline, it's not theirs (a circle would have reached Vancouver Island).
    victoria = msgspec.structs.replace(own, lat=49.0, lon=-125.5)
    assert not online_atc.covers(seattle, victoria)


def test_the_copilot_keeps_to_the_intercom_on_the_networks_frequency():
    from test_crew_monitor import FakeEngine, crew, said

    engine = FakeEngine()
    engine.cfg.online_network = "ivao"
    engine.online_muted = True
    pm = crew(engine)
    item = pm.deck.submit("crew", "request", "ask:1", 1.0, text="Seattle Center, request flight level 380")
    pm._radio_queue.append(item)
    out = pm._radio_due(2.0)
    assert said(out) == ['We\'re on IVAO, that call\'s yours: "Seattle Center, request flight level 380".']
    assert not [o for o in out if isinstance(o, Transcript)] and pm._radio_queue == []
    assert said(pm._one_call(3.0, "ground")) == ["We're on IVAO, you'll have to call them yourself."]


# --- the networks' top-down rule: a controller works the positions below theirs nobody's on, in their own airspace --


def test_an_approach_with_no_tower_or_ground_works_all_three_at_its_airports():
    engine, own = parked_engine()  # parked at Paine, which Seattle Approach's area takes in
    approach, tower = ctl("SEA_APP", 119.2, lat=47.45, lon=-122.31), ctl("KPAE_TWR", 132.95)
    assert online_atc.best([approach], own, "PARKED") is approach  # delivery's job
    assert online_atc.best([approach], own, "TAXI_OUT") is approach  # ground's
    assert online_atc.best([approach, tower], own, "TAXI_OUT") is tower  # a tower with no ground does ground
    assert online_atc.best([approach, tower], own, "RUNWAY_HOLD") is tower


def test_a_tower_works_only_its_own_field_and_zone_never_an_approach():
    engine, own = parked_engine()
    boeing_tower = ctl("KBFI_TWR", 120.6, lat=47.53, lon=-122.30)  # Boeing Field's tower, 20 nm off
    assert online_atc.best([boeing_tower], own, "TAXI_OUT") is None
    tower = ctl("KPAE_TWR", 132.95)
    climbing = msgspec.structs.replace(own, on_ground=False, alt_agl_ft=6000, alt_indicated_ft=6600, lat=LAT + 0.05)
    assert online_atc.best([tower], climbing, "DEPARTURE") is None  # the approach's airspace: not the tower's
    just_off = msgspec.structs.replace(climbing, alt_agl_ft=1200, alt_indicated_ft=1800)
    assert online_atc.best([tower], just_off, "DEPARTURE") is tower  # still in its zone
    centre = ctl("ZSE_CTR", 124.2, lat=44.0, lon=-121.0, kind="CTR")
    assert online_atc.best([tower, centre], climbing, "DEPARTURE") is centre


def test_a_centre_with_nobody_below_does_everything_and_a_ground_never_works_the_runway():
    engine, own = parked_engine()
    centre, ground = ctl("ZSE_CTR", 124.2, lat=44.0, lon=-121.0, kind="CTR"), ctl("KPAE_GND", 121.8)
    for phase in ("PARKED", "TAXI_OUT", "RUNWAY_HOLD", "TAKEOFF"):
        assert online_atc.best([centre], own, phase) is centre
    assert online_atc.best([ground], own, "RUNWAY_HOLD") is None
    assert online_atc.best([ground, centre], own, "RUNWAY_HOLD") is centre


def test_on_their_ground_with_nobody_online_for_the_runway_the_pilot_is_sent_to_localtcs_tower():
    engine, own = parked_engine(online_network="vatsim")
    engine.handle(OnlineAtc(t=1, network="vatsim", controllers=(ctl("KPAE_GND", 121.8),)))
    engine.handle(msgspec.structs.replace(own, t=2, com1_mhz=121.8))
    assert engine.online_muted
    engine.state.phase = "RUNWAY_HOLD"
    (gap,) = alerts(engine.handle(msgspec.structs.replace(own, t=3, com1_mhz=121.8)), "online_gap")
    local = engine.online_gap
    assert local.controller == "tower" and local.airport == "KPAE"
    assert gap.detail == f"No VATSIM controller for this from KPAE_GND: LocalTC's {local.station}, set {local.mhz:.3f} on COM1"


def test_the_flights_frequency_table_says_who_works_each_airspace_top_down():
    from helpers.airports import kbfi

    from localtc.sim_api import AirportData

    engine, own = parked_engine(destination="KBFI", cruise_ft=12000, online_network="vatsim")
    engine.handle(AirportData(t=0, airport=kbfi()))

    def table():
        return [(r["space"], r["who"], r["station"], r["mhz"], r["top_down"]) for r in engine.frequency_plan()]

    local = table()  # nobody online: every airspace LocalTC's, in the order it's flown
    assert [space for space, *_ in local] == ["KPAE Delivery", "KPAE Ground", "KPAE Tower", "KPAE Departure",
                                              "Seattle Center (KZSE)", "KBFI Arrival", "KBFI Tower", "KBFI Ground"]
    assert {who for _, who, *_ in local} == {"LocalTC"}

    # Seattle Approach online, Paine's tower too: Paine's ground and delivery are the tower's (top-down), Boeing's
    # tower and ground the approach's; the centre stays LocalTC's.
    engine.handle(OnlineAtc(t=1, network="vatsim", controllers=(ctl("SEA_APP", 119.2, lat=47.45, lon=-122.31),
                                                                 ctl("KPAE_TWR", 132.95))))
    rows = {space: (who, station, top_down) for space, who, station, _, top_down in table()}
    assert rows["KPAE Delivery"] == ("VATSIM", "KPAE_TWR", True)
    assert rows["KPAE Tower"] == ("VATSIM", "KPAE_TWR", False)
    assert rows["KPAE Departure"] == ("VATSIM", "SEA_APP", False)
    assert rows["KBFI Ground"] == ("VATSIM", "SEA_APP", True)
    assert rows["Seattle Center (KZSE)"][0] == "LocalTC"
    # ... and the ATC flies by the same rule: parked at Paine, it's the tower the pilot is sent to.
    assert online_atc.best(engine.online, own, "PARKED").callsign == "KPAE_TWR"
