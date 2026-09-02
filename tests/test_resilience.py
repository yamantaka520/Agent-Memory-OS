"""Phase D — resilience: bundle-import fuzzing, migration upgrade path, backup rotation."""

from __future__ import annotations

import json
import sqlite3

import pytest

from agent_memory_os import MemoryClient
from agent_memory_os.cli import _rotate_backups
from agent_memory_os.sync import export_bundle, import_bundle


# ---------- bundle import: malformed input must never corrupt state ----------

def _seed(tmp_path):
    c = MemoryClient(home=tmp_path)
    c.store.register_agent("a1")
    c.add("baseline", owner="a1", visibility=["global"])
    return c


def _snapshot(store):
    return {
        "memories": store.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
        "teams": store.conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0],
        "members": store.conn.execute("SELECT COUNT(*) FROM team_members").fetchone()[0],
    }


@pytest.mark.parametrize("bad", [
    "this is not json at all\n",
    json.dumps({"kind": "memory"}) + "\n",                 # missing required id
    json.dumps({"kind": "memory", "id": "m", "updated_at": 12345}) + "\n",  # wrong type
    "\x00\x01\x02 binary garbage\n",
])
def test_malformed_bundle_line_rolls_back_atomically(tmp_path, bad):
    c = _seed(tmp_path)
    before = _snapshot(c.store)
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({"kind": "bundle", "version": 3}) + "\n" + bad, encoding="utf-8")
    with pytest.raises(Exception):
        import_bundle(c.store, str(path), org_scope="full")
    # A corrupt line must roll the WHOLE merge back — no partial state.
    assert _snapshot(c.store) == before


def test_malformed_members_field_cannot_replace_acl_rosters(tmp_path):
    """A malformed authoritative roster must fail before deleting membership."""
    c = _seed(tmp_path)
    c.store.create_team("t")
    c.store.add_team_member("t", "a1")
    c.store.create_project("p", "t")
    c.store.add_project_member("p", "a1")
    path = tmp_path / "m.jsonl"
    path.write_text(
        json.dumps({"kind": "bundle", "version": 3}) + "\n"
        + json.dumps({"kind": "team", "id": "t", "name": "t",
                      "members": "abc", "updated_at": "2020-01-01T00:00:00+00:00"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="members"):
        import_bundle(c.store, str(path), org_scope="full")
    assert c.store.get_team("t")["members"] == ["a1"]
    assert c.store.get_project("p")["members"] == ["a1"]


def test_bad_header_rejected(tmp_path):
    c = _seed(tmp_path)
    path = tmp_path / "h.jsonl"
    path.write_text(json.dumps({"kind": "notbundle", "version": 3}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        import_bundle(c.store, str(path))


def test_unknown_bundle_version_rejected(tmp_path):
    c = _seed(tmp_path)
    path = tmp_path / "v.jsonl"
    path.write_text(json.dumps({"kind": "bundle", "version": 999}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        import_bundle(c.store, str(path))


def test_oversized_and_unicode_fields_do_not_crash(tmp_path):
    """A huge content field and exotic unicode must import cleanly, not crash."""
    c = _seed(tmp_path)
    src = MemoryClient(home=tmp_path / "src")
    src.store.register_agent("a1")
    src.add("x" * 500_000, owner="a1", visibility=["global"])       # 0.5 MB content
    src.add("emoji 🧠🔐 and 中文 and ​ zero-width", owner="a1", visibility=["global"])
    out = tmp_path / "big.jsonl"
    export_bundle(src.store, out)
    stats = import_bundle(c.store, str(out), org_scope="full")
    assert stats["memories_added"] == 2


def test_unknown_entry_kind_is_ignored(tmp_path):
    c = _seed(tmp_path)
    before = _snapshot(c.store)
    path = tmp_path / "u.jsonl"
    path.write_text(
        json.dumps({"kind": "bundle", "version": 3}) + "\n"
        + json.dumps({"kind": "from_the_future", "data": 1}) + "\n",
        encoding="utf-8",
    )
    import_bundle(c.store, str(path), org_scope="full")  # must not raise
    assert _snapshot(c.store) == before


# ---------- migration upgrade path ----------

def test_migrations_apply_forward_and_preserve_data(tmp_path):
    """A DB created at an older schema (acl clock absent, user_version reset)
    migrates forward on reopen with data intact and integrity holding."""
    c = MemoryClient(home=tmp_path)
    c.store.register_agent("a1")
    c.store.create_team("t"); c.store.add_team_member("t", "a1")
    m = c.add("survivor", owner="a1", visibility=["team:t"])
    c.close()

    # Simulate an older on-disk schema: drop the acl clock + mark migration 15
    # as not-yet-applied (the framework tracks versions in schema_migrations).
    db = sqlite3.connect(tmp_path / "memories.db")
    db.execute("ALTER TABLE memories DROP COLUMN acl_updated_at")
    db.execute("DELETE FROM schema_migrations WHERE version = 15")
    db.commit(); db.close()

    # Reopen → migrations re-run → column back, data preserved.
    c2 = MemoryClient(home=tmp_path)
    cols = {r[1] for r in c2.store.conn.execute("PRAGMA table_info(memories)")}
    assert "acl_updated_at" in cols
    assert c2.get(m.id) is not None and c2.get(m.id).content == "survivor"
    assert c2.integrity_check()["ok"] is True
    # Backfill must have set acl_updated_at (not NULL) so sync LWW works.
    val = c2.store.conn.execute(
        "SELECT acl_updated_at FROM memories WHERE id = ?", (m.id,)).fetchone()[0]
    assert val


# ---------- backup rotation ----------

def test_rotate_backups_keeps_newest(tmp_path):
    import os
    import time

    made = []
    for i in range(5):
        p = tmp_path / f"mem-2026-07-0{i}.db"
        p.write_text(f"backup {i}")
        # Stagger mtimes so "newest" is well-defined.
        os.utime(p, (time.time() + i, time.time() + i))
        made.append(p)
    unrelated = tmp_path / "other.db"; unrelated.write_text("keep me")

    removed = _rotate_backups(made[-1], keep=2)
    survivors = {p.name for p in tmp_path.glob("*.db")}
    # Two newest mem-* survive, plus the unrelated file is never touched.
    assert "mem-2026-07-04.db" in survivors and "mem-2026-07-03.db" in survivors
    assert "other.db" in survivors
    assert len(removed) == 3


def test_rotate_backups_never_deletes_live_db(tmp_path):
    """CRITICAL: rotation must never delete the live memories.db, even when the
    backup name prefix collides with it (mem-*.db vs memories.db)."""
    live = tmp_path / "memories.db"; live.write_text("LIVE DATA")
    (tmp_path / "memories.db-wal").write_text("wal")
    for i in range(4):
        (tmp_path / f"mem-2026-07-0{i}.db").write_text(f"b{i}")
    # keep=1 with the aggressive 'mem' prefix — the live DB must survive.
    _rotate_backups(tmp_path / "mem-2026-07-03.db", keep=1)
    assert live.exists() and live.read_text() == "LIVE DATA"
    assert (tmp_path / "memories.db-wal").exists()


def test_rotate_backups_prefix_series_memories_dash(tmp_path):
    """A backup series literally named 'memories-<date>.db' still rotates without
    touching the live 'memories.db'."""
    live = tmp_path / "memories.db"; live.write_text("LIVE")
    import os
    import time
    made = []
    for i in range(3):
        p = tmp_path / f"memories-2026-01-0{i}.db"; p.write_text(f"b{i}")
        os.utime(p, (time.time() + i, time.time() + i))
        made.append(p)
    removed = _rotate_backups(made[-1], keep=1)
    assert live.exists() and live.read_text() == "LIVE"
    assert len(removed) == 2
