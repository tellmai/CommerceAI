from decimal import Decimal
import hashlib
import json
from pathlib import Path


def test_attributed_real_fixture_import_and_controls(client):
    directory = Path(__file__).parent / "fixtures"
    sample = directory / "uci_excerpt.csv"
    control = json.loads((directory / "uci_control.json").read_text())
    assert hashlib.file_digest(sample.open("rb"), "sha256").hexdigest() == control["sha256"]
    client.headers["X-Workspace"] = "public"
    source = client.post(
        "/api/v1/sources",
        json={
            "name": "UCI CI fixture",
            "platform": "UCI",
            "permission_scope": ["transactions"],
            "permission_note": "Daqing Chen (2015), CC BY 4.0",
            "license": "CC BY 4.0",
            "origin_url": "https://archive.ics.uci.edu/dataset/352/online+retail",
        },
    )
    assert source.status_code == 201
    with sample.open("rb") as file:
        uploaded = client.post(
            "/api/v1/imports/preview",
            data={"source_id": source.json()["id"], "kind": "transactions"},
            files={"file": (sample.name, file, "text/csv")},
        )
    assert uploaded.status_code == 200
    headers = uploaded.json()["preview"]["headers"]
    confirmed = client.post(
        f"/api/v1/imports/{uploaded.json()['id']}/confirm",
        json={"mapping": {key: key for key in headers}, "timezone": "Europe/London"},
    )
    assert confirmed.status_code == 202, confirmed.text
    from app.worker import run_once

    run_once()
    job = client.get("/api/v1/jobs/" + confirmed.json()["id"]).json()
    assert job["status"] == "SUCCEEDED", job
    dashboard = client.get("/api/v1/dashboard").json()["history"]
    assert dashboard["rows"] == control["rows"]
    assert dashboard["quantity"] == control["quantity"]
    assert Decimal(dashboard["amount"]) == Decimal(control["amount"])
    assert dashboard["unmatched_cancellations"] == control["cancelled"]
    assert dashboard["contribution"] is None
