"""Operator-visible federation results and confirmed import rollback."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_memory_os import MemoryClient
from agent_memory_os.web_app import create_app
from agent_memory_os.web_ui import PAGE


def test_import_failure_reports_rollback_after_valid_record(tmp_path):
    source = MemoryClient(home=tmp_path / "source")
    try:
        first = source.add("First peer record", owner="peer", visibility=["global"])
        second = source.add("Invalid peer record", owner="peer", visibility=["global"])
        path = tmp_path / "bundle.jsonl"
        source.export_bundle(path)
    finally:
        source.close()
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    next(row for row in lines if row.get("id") == second.id)["visibility"] = "not-json"

    with TestClient(create_app(home=tmp_path / "target", token="FULL")) as web:
        headers = {"Authorization": "Bearer FULL"}
        survivor = web.post("/api/memories", json={"content": "Local survivor"}, headers=headers).json()
        failed = web.post(
            "/api/sync/import", headers=headers,
            content="\n".join(json.dumps(line) for line in lines) + "\n",
        )
        assert failed.status_code == 400
        assert failed.headers["X-AMOS-Import-Outcome"] == "rolled-back"
        assert "visibility" in failed.json()["detail"]
        records = web.get("/api/memories", headers=headers).json()["memories"]
        assert {record["id"] for record in records} == {survivor["id"]}
        assert first.id not in {record["id"] for record in records}


def test_import_success_and_auth_failure_do_not_claim_rollback(tmp_path):
    with TestClient(create_app(home=tmp_path, token="FULL")) as web:
        body = '{"kind":"bundle","version":3}\n'
        denied = web.post("/api/sync/import", content=body)
        assert denied.status_code == 401
        assert "X-AMOS-Import-Outcome" not in denied.headers
        accepted = web.post(
            "/api/sync/import", content=body,
            headers={"Authorization": "Bearer FULL"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["memories_added"] == 0
        assert "X-AMOS-Import-Outcome" not in accepted.headers


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed for the UI workflow test")
def test_federation_result_rendering_and_workflow():
    node = shutil.which("node")
    assert node is not None
    result = subprocess.run(
        [node, str(Path(__file__).with_suffix(".cjs"))], input=PAGE,
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
