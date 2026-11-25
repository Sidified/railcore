import io
import json

import pytest

from railcore.api import RailCoreApp
from railcore.booking import BookingService
from railcore.storage import InMemoryRepository



class Client:
    def __init__(self, app):
        self.app = app

    def call(self, method, path, body=None, query=""):
        raw = json.dumps(body).encode() if body is not None else b""
        environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query,
                   "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw)}
        captured = {}

        def start_response(status, headers):
            captured["status"] = int(status.split()[0])
            captured["headers"] = dict(headers)

        data = b"".join(self.app(environ, start_response))
        assert captured["headers"]["Content-Type"] == "application/json"
        return captured["status"], json.loads(data)

    def get(self, path, query=""):
        return self.call("GET", path, query=query)

    def post(self, path, body=None):
        return self.call("POST", path, body)


@pytest.fixture
def client(clock):
    return Client(RailCoreApp(BookingService(InMemoryRepository(), clock=clock)))


BOOK = {"train": "22401", "date": "2026-12-10", "from": "NDLS", "to": "BPL", "class": "SL",
        "passengers": [{"name": "Asha", "age": 30, "gender": "F", "berth_preference": "LB"},
                       {"name": "Ravi", "age": 32, "gender": "M"}]}


def test_health_and_trains(client):
    assert client.get("/health") == (200, {"status": "ok"})
    status, trains = client.get("/trains", "from=BPL&to=JHS")
    assert status == 200 and [t["number"] for t in trains] == ["11071"]
    status, train = client.get("/trains/22401")
    assert train["classes"]["SL"] == 6 and train["stops"][0]["dep"] == "20:00"


def test_availability_and_fare(client):
    assert client.get("/availability", "train=22401&date=2026-12-10&class=SL") == \
        (200, {"availability": "AVAILABLE-0234"})
    status, body = client.get("/fare", "train=22401&date=2026-12-10&from=NDLS&to=BPL&class=SL&passengers=2")
    assert status == 200 and body["total"] == "820.00"


def test_booking_lifecycle(client):
    status, booking = client.post("/bookings", BOOK)
    assert status == 201
    pnr = booking["pnr"]
    assert booking["passengers"][0]["current_status"]["label"] == "CNF/S1/25/LB"
    assert client.get(f"/bookings/{pnr}")[1]["state"] == "ACTIVE"
    status, quote = client.get(f"/bookings/{pnr}/refund", "passengers=2")
    assert quote["refunds"]["2"]["refund"] == "290.00"
    status, result = client.post(f"/bookings/{pnr}/cancel", {"passengers": [2]})
    assert status == 200 and result["total_refund"] == "290.00"
    assert result["booking"]["passengers"][1]["current_status"]["kind"] == "CAN"
    status, events = client.get("/events")
    assert [e["type"] for e in events] == ["booking.created", "passenger.cancelled"]


def test_chart_endpoint(client):
    status, body = client.post("/runs/22401/2026-12-10/chart", {"kind": "first"})
    assert status == 200 and body["kind"] == "first"
    status, body = client.post("/runs/22401/2026-12-10/chart", {"kind": "first"})
    assert status == 409 and body["error"] == "invalid_state"


@pytest.mark.parametrize("method,path,body,query,status,error", [
    ("GET", "/nope", None, "", 404, "not_found"),
    ("DELETE", "/bookings", None, "", 405, "method_not_allowed"),
    ("GET", "/availability", None, "train=22401", 400, "missing_parameter"),
    ("GET", "/availability", None, "train=22401&date=10-12-2026&class=SL", 400, "bad_date"),
    ("GET", "/availability", None, "train=22401&date=2026-12-10&class=ZZ", 400, "validation_error"),
    ("GET", "/fare", None, "train=22401&date=2026-12-10&from=NDLS&to=BPL&class=SL&passengers=9", 400,
     "bad_parameter"),
    ("POST", "/bookings", {"train": "22401"}, "", 400, "missing_field"),
    ("POST", "/bookings", dict(BOOK, passengers=[{"name": "A"}]), "", 400, "bad_field"),
    ("POST", "/bookings", dict(BOOK, passengers=[{"name": "A", "age": "x", "gender": "M"}]), "", 400,
     "bad_field"),
    ("POST", "/bookings", dict(BOOK, date="2027-03-01"), "", 409, "booking_closed"),
    ("GET", "/bookings/2999999999", None, "", 404, "not_found"),
    ("GET", "/trains/99999", None, "", 404, "not_found"),
])
def test_errors(client, method, path, body, query, status, error):
    got_status, got = client.call(method, path, body, query)
    assert (got_status, got["error"]) == (status, error)


def test_bad_json_body(client):
    environ = {"REQUEST_METHOD": "POST", "PATH_INFO": "/bookings", "QUERY_STRING": "",
               "CONTENT_LENGTH": "5", "wsgi.input": io.BytesIO(b"{nope")}
    out = {}
    body = b"".join(client.app(environ, lambda s, h: out.update(status=s)))
    assert out["status"].startswith("400") and json.loads(body)["error"] == "bad_json"
    environ["wsgi.input"], environ["CONTENT_LENGTH"] = io.BytesIO(b"[1]"), "3"
    body = b"".join(client.app(environ, lambda s, h: out.update(status=s)))
    assert json.loads(body)["error"] == "bad_json"


def test_regret_is_conflict(client):
    one = {"name": "X", "age": 40, "gender": "M"}
    for _ in range(4):  # 22 general + 30 waitlist on 1A, in groups of 6 / 4
        client.post("/bookings", dict(BOOK, **{"class": "1A"}, passengers=[one] * 6))
    client.post("/bookings", dict(BOOK, **{"class": "1A"}, passengers=[one] * 4))
    for _ in range(4):
        client.post("/bookings", dict(BOOK, **{"class": "1A"}, passengers=[one] * 6))
    status, body = client.post("/bookings", dict(BOOK, **{"class": "1A"}, passengers=[one]))
    assert (status, body["error"]) == (409, "no_availability")
