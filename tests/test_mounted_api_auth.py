"""Auth uses the routed path; fleet signatures still bind the request target."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_memory_os import MemoryClient, crypto, tokens
from agent_memory_os.web_app import create_app

PRIVATE_NOTE = "Mount-prefix private memory."


@pytest.fixture(params=["", "/amos", "/api/amos"], ids=["direct", "mounted", "api-prefix"])
def auth_node(tmp_path, request, monkeypatch):
    for name in (
        "AGENT_MEMORY_WEB_TOKEN",
        "AGENT_MEMORY_WEB_READONLY_TOKEN",
        "AGENT_MEMORY_WEB_SYNC_TOKEN",
        "AGENT_MEMORY_SYNC_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    tokens.save_token(tmp_path, "SYNC", tier="sync")
    seed = MemoryClient(home=tmp_path)
    try:
        seed.add(PRIVATE_NOTE, owner="alice", visibility=[])
    finally:
        seed.close()

    prefix = request.param
    app = create_app(home=tmp_path, token="FULL", readonly_token="RO")
    # Mounted apps do not receive the parent's lifespan events. Keep the
    # child's lifespan open as well so its database connection is closed.
    with TestClient(app) as direct:
        if not prefix:
            yield direct, prefix
        else:
            parent = FastAPI()
            parent.mount(prefix, app)
            with TestClient(parent) as mounted:
                yield mounted, prefix


def _grant_fleet_key(home, caps, keypair=None):
    keypair = keypair or crypto.generate_fleet_keypair()
    client = MemoryClient(home=home)
    try:
        client.store.grant_fleet_admin(keypair["public_key"], caps)
    finally:
        client.close()
    return keypair


def test_token_gate_protects_private_content_at_mount_prefix(auth_node):
    http, prefix = auth_node
    assert http.get(f"{prefix}/").status_code == 200
    assert http.get(f"{prefix}/health").status_code == 200

    target = f"{prefix}/api/memories"
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        denied = http.get(target, headers=headers)
        assert denied.status_code == 401
        assert PRIVATE_NOTE not in denied.text

    allowed = http.get(target, headers={"Authorization": "Bearer FULL"})
    assert allowed.status_code == 200
    assert [record["content"] for record in allowed.json()["memories"]] == [PRIVATE_NOTE]


def test_readonly_token_keeps_method_restrictions_at_mount_prefix(auth_node):
    http, prefix = auth_node
    headers = {"Authorization": "Bearer RO"}
    target = f"{prefix}/api/memories"
    allowed = http.get(target, headers=headers)
    assert allowed.status_code == 200
    assert [record["content"] for record in allowed.json()["memories"]] == [PRIVATE_NOTE]

    denied = http.post(target, headers=headers, json={"content": "Unauthorized write."})
    assert denied.status_code == 403
    records = http.get(target, headers={"Authorization": "Bearer FULL"}).json()["memories"]
    assert [record["content"] for record in records] == [PRIVATE_NOTE]


def test_sync_token_keeps_route_scope_at_mount_prefix(auth_node):
    http, prefix = auth_node
    headers = {"Authorization": "Bearer SYNC"}
    assert http.get(f"{prefix}/api/node", headers=headers).status_code == 200
    exported = http.get(f"{prefix}/api/sync/export", headers=headers)
    assert exported.status_code == 200
    assert PRIVATE_NOTE not in exported.text
    imported = http.post(
        f"{prefix}/api/sync/import", headers=headers, content=exported.text,
    )
    assert imported.status_code == 200

    denied = http.get(f"{prefix}/api/memories", headers=headers)
    assert denied.status_code == 403
    assert PRIVATE_NOTE not in denied.text
    assert http.post(f"{prefix}/api/sync/run", headers=headers).status_code == 403


def test_pairing_redeem_keeps_its_post_only_auth_exception(auth_node):
    http, prefix = auth_node
    target = f"{prefix}/api/pairing/redeem"
    # Reaching request validation proves bearer auth did not intercept the
    # exempt POST, without redeeming an invite or bypassing its own checks.
    assert http.post(target, json={}).status_code == 422
    assert http.get(target).status_code == 401


def test_fleet_capabilities_use_the_routed_path(auth_node, tmp_path):
    http, prefix = auth_node
    keypair = _grant_fleet_key(tmp_path, ["manage"])
    stats_target = f"{prefix}/api/stats"
    stats_headers = crypto.fleet_sign_headers(keypair, "GET", stats_target)
    assert http.get(stats_target, headers=stats_headers).status_code == 200

    target = f"{prefix}/api/memories?limit=1"
    denied_headers = crypto.fleet_sign_headers(keypair, "GET", target)
    denied = http.get(target, headers=denied_headers)
    assert denied.status_code == 403
    assert "read-private" in denied.json()["detail"]
    assert PRIVATE_NOTE not in denied.text

    _grant_fleet_key(tmp_path, ["manage", "read-private"], keypair)
    allowed_headers = crypto.fleet_sign_headers(keypair, "GET", target)
    allowed = http.get(target, headers=allowed_headers)
    assert allowed.status_code == 200
    assert [record["content"] for record in allowed.json()["memories"]] == [PRIVATE_NOTE]
    audit = MemoryClient(home=tmp_path)
    try:
        entries = audit.store.org_audit_log(limit=10)
    finally:
        audit.close()
    assert any(
        entry["action"] == "fleet_op"
        and entry["actor"] == f"fleet:{keypair['key_id']}"
        and entry["detail"] == f"GET {target}"
        for entry in entries
    )


def test_fleet_signature_still_binds_full_target_and_rejects_replay(auth_node, tmp_path):
    http, prefix = auth_node
    keypair = _grant_fleet_key(tmp_path, ["manage", "read-private"])
    target = f"{prefix}/api/memories?limit=1"
    headers = crypto.fleet_sign_headers(keypair, "GET", target)
    assert http.get(target, headers=headers).status_code == 200
    replay = http.get(target, headers=headers)
    assert replay.status_code == 403
    assert "replayed nonce" in replay.json()["detail"]

    query_headers = crypto.fleet_sign_headers(keypair, "GET", target)
    tampered = http.get(f"{prefix}/api/memories?limit=2", headers=query_headers)
    assert tampered.status_code == 403
    assert "invalid signature" in tampered.json()["detail"]

    if prefix:
        unprefixed_headers = crypto.fleet_sign_headers(keypair, "GET", "/api/memories?limit=1")
        missing_prefix = http.get(target, headers=unprefixed_headers)
        assert missing_prefix.status_code == 403
        assert "invalid signature" in missing_prefix.json()["detail"]
