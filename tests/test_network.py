from datetime import date, datetime

import pytest

from railcore.config import Config, default_config
from railcore.domain.classes import TravelClass
from railcore.domain.network import Network
from railcore.errors import NotFound, ValidationError


@pytest.fixture(scope="module")
def network():
    return Network.from_config()


def test_distance_between_stops(network):
    train = network.train("22401")
    assert train.distance("NDLS", "BPL") == 701
    assert train.distance("AGC", "ET") == 598


def test_segment_must_go_forward(network):
    with pytest.raises(ValidationError):
        network.train("22401").distance("BPL", "NDLS")
    with pytest.raises(ValidationError):
        network.train("22401").distance("NDLS", "JBP")


def test_departure_crosses_midnight(network):
    train = network.train("22401")
    assert train.departure("NDLS", date(2026, 12, 10)) == datetime(2026, 12, 10, 20, 0)
    assert train.departure("BPL", date(2026, 12, 10)) == datetime(2026, 12, 11, 6, 20)
    assert train.arrival("NGP", date(2026, 12, 10)) == datetime(2026, 12, 11, 12, 30)


def test_no_departure_from_terminus(network):
    with pytest.raises(ValidationError):
        network.train("22401").departure("NGP", date(2026, 12, 10))


def test_runs_on(network):
    rajdhani = network.train("12099")
    assert rajdhani.runs_on_date(date(2026, 12, 7))      # Monday
    assert not rajdhani.runs_on_date(date(2026, 12, 8))  # Tuesday


def test_unknown_train(network):
    with pytest.raises(NotFound):
        network.train("99999")


def test_trains_between(network):
    numbers = [t.number for t in network.trains_between("JHS", "BPL")]
    assert numbers == ["12001", "12099", "22401"]
    assert [t.number for t in network.trains_between("BPL", "JHS")] == ["11071"]


def test_composition_and_ac_only(network):
    assert network.train("12099").carries(TravelClass.FIRST_AC)
    assert not network.train("12099").carries(TravelClass.SLEEPER)
    assert network.train("12099").is_ac_only
    assert not network.train("11071").is_ac_only


def test_bad_station_in_config_is_rejected():
    cfg = default_config()
    trains = [dict(cfg.trains[0], stops=[dict(s) for s in cfg.trains[0]["stops"]])]
    trains[0]["stops"][1]["station"] = "XXX"
    with pytest.raises(ValueError):
        Network.from_config(Config(cfg.stations, trains, cfg.fares, cfg.quotas, cfg.policy))


def test_distances_must_increase():
    cfg = default_config()
    trains = [dict(cfg.trains[0], stops=[dict(s) for s in cfg.trains[0]["stops"]])]
    trains[0]["stops"][2]["km"] = 100
    with pytest.raises(ValueError):
        Network.from_config(Config(cfg.stations, trains, cfg.fares, cfg.quotas, cfg.policy))
