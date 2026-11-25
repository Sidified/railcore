import json

from railcore.cli import main

NOW = ["--now", "2026-12-01T09:00"]


def run(capsys, db, *args):
    code = main(["--db", str(db), *NOW, *args])
    out, err = capsys.readouterr()
    return code, json.loads(out or err)


def test_book_status_cancel(tmp_path, capsys):
    db = tmp_path / "cli.db"
    code, booking = run(capsys, db, "book", "22401", "2026-12-10", "NDLS", "BPL", "SL",
                        "-p", "Asha,30,F,LB", "-p", "Ravi,32,M")
    assert code == 0 and booking["total_fare"] == "820.00"
    pnr = booking["pnr"]
    code, status = run(capsys, db, "pnr", pnr)
    assert status["passengers"][1]["current_status"]["label"] == "CNF/S1/11/UB"
    code, quote = run(capsys, db, "cancel", pnr, "--passengers", "2", "--quote")
    assert quote["2"]["refund"] == "290.00"
    code, result = run(capsys, db, "cancel", pnr, "--passengers", "2")
    assert result["total_refund"] == "290.00"
    code, events = run(capsys, db, "events")
    assert [e["type"] for e in events] == ["booking.created", "passenger.cancelled"]


def test_queries(tmp_path, capsys):
    db = tmp_path / "cli.db"
    assert run(capsys, db, "availability", "22401", "2026-12-10", "1A")[1] == {"availability": "AVAILABLE-0022"}
    assert run(capsys, db, "fare", "22401", "2026-12-10", "NDLS", "BPL", "SL")[1]["total"] == "410.00"
    assert [t["number"] for t in run(capsys, db, "trains", "--from", "BPL", "--to", "JHS")[1]] == ["11071"]
    assert run(capsys, db, "chart", "22401", "2026-12-10")[1]["kind"] == "first"


def test_errors_exit_non_zero(tmp_path, capsys):
    code, err = run(capsys, tmp_path / "cli.db", "pnr", "2999999999")
    assert code == 1 and err["error"] == "not_found"
