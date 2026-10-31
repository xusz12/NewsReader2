"""Snapshot retention and generation timing use isolated databases and fake models."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from types import SimpleNamespace

import pytest
import periodic_reports as pr
from test_periodic_reports import db
from test_report_comparison import service, create, rows


def state(db):
    with closing(db()) as conn:
        return dict(conn.execute("SELECT * FROM periodic_report_snapshot_cleanup").fetchone())


def age(db, captured="2026-10-29T12:00:00+08:00"):
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_report_snapshots SET captured_at=?", (captured,))
        conn.execute("UPDATE periodic_reports SET snapshot_captured_at=?", (captured,))


def test_timer_is_durable_and_runs_once_after_resume(service, db):
    first = create(service)
    assert service.run_one()
    age(db)
    original = rows(db)[0]
    assert state(db)["next_run_at"] == "2026-11-06T10:00:00+08:00"
    with closing(db()) as conn:
        pr.initialize(conn, pr.local("2026-11-05T12:00:00+08:00"))
    service.cleanup_snapshots(pr.local("2026-11-06T09:59:59+08:00"))
    assert rows(db)[0]["snapshot_id"]
    resumed = pr.local("2026-11-20T12:00:00+08:00")
    service.cleanup_snapshots(resumed)
    after = rows(db)[0]
    assert after["snapshot_id"] is None
    for key in ("result_json", "config_json", "updated_at", "generation_seconds", "snapshot_captured_at"):
        assert original[key] == after[key]
    assert pr.serialize(after, detail=True)["content"]["sources"]
    assert state(db)["next_run_at"] == "2026-11-27T12:00:00+08:00"
    assert state(db)["removed_count"] == 1
    service.cleanup_snapshots(resumed)
    assert state(db)["removed_count"] == 1  # No second run / cursor drift.
    # A new comparison after expiry captures fresh input, leaving the old report intact.
    second = create(service)
    assert service.run_one()
    current = {row["id"]: row for row in rows(db)}
    assert current[second["id"]]["snapshot_id"] != original["snapshot_id"]
    assert current[first["id"]]["result_json"] == original["result_json"]


@pytest.mark.parametrize("status", ["pending", "running", "failed"])
def test_unfinished_comparisons_protect_window_even_before_attachment(service, db, status):
    create(service)
    assert service.run_one()
    comparison = create(service)
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_reports SET status=? WHERE id=?", (status, comparison["id"]))
    age(db)
    service.cleanup_snapshots(pr.local("2026-11-06T10:00:00+08:00"))
    assert state(db)["removed_count"] == 0
    assert any(row["snapshot_id"] for row in rows(db))
    with closing(db()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM periodic_report_snapshots").fetchone()[0] == 1
    # Once the report is explicitly removed, the following cleanup may release input.
    with closing(db()) as conn, conn:
        conn.execute("DELETE FROM periodic_reports WHERE id=?", (comparison["id"],))
    service.cleanup_snapshots(pr.local("2026-11-13T10:00:00+08:00"))
    assert rows(db)[0]["snapshot_id"] is None


def test_retention_handles_recent_shared_and_legacy_materials(service, db):
    first = create(service)
    assert service.run_one()
    age(db, "2026-11-02T12:00:00+08:00")
    legacy = create(service, start="2026-10-28T00:00:00+08:00", end="2026-10-29T00:00:00+08:00")
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_reports SET status='done',snapshot_json='{}',updated_at='2026-10-29T12:00:00+08:00' WHERE id=?", (legacy["id"],))
    service.cleanup_snapshots(pr.local("2026-11-06T10:00:00+08:00"))
    current = {row["id"]: row for row in rows(db)}
    assert current[first["id"]]["snapshot_id"]
    assert current[legacy["id"]]["snapshot_json"] is None
    assert state(db)["removed_count"] == 1


def test_cleanup_rolls_back_on_failure_and_retries_without_blocking_schedule(service, db):
    create(service)
    assert service.run_one()
    age(db)
    original = rows(db)[0]["snapshot_id"]
    with closing(db()) as conn, conn:
        conn.execute("""CREATE TRIGGER fail_cleanup BEFORE DELETE ON periodic_report_snapshots
            BEGIN SELECT RAISE(ABORT,'private database details'); END""")
    at = pr.local("2026-11-06T10:00:00+08:00")
    service.schedule_once(at)
    assert rows(db)[0]["snapshot_id"] == original  # Earlier UPDATE was rolled back.
    assert service.last_schedule_success == at
    assert state(db)["error"] == "report_snapshot_cleanup_failed"
    assert state(db)["retry_at"] == "2026-11-06T10:05:00+08:00"
    assert state(db)["last_run_at"] is None
    entry = next(row for row in service.observation()["schedules"] if row["kind"] == "snapshot_cleanup")
    assert entry["error"] == "report_snapshot_cleanup_failed"
    assert entry["next_run_at"] == state(db)["retry_at"]
    with closing(db()) as conn, conn:
        conn.execute("DROP TRIGGER fail_cleanup")
    service.cleanup_snapshots(at+timedelta(minutes=4))
    assert state(db)["last_run_at"] is None
    service.cleanup_snapshots(at+timedelta(minutes=5))
    assert state(db)["error"] == "" and state(db)["removed_count"] == 1


def test_index_refresh_never_blocks_scheduler_for_cleanup(service, db):
    with service.index_lock, ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(service.cleanup_snapshots, pr.local("2026-11-06T10:00:00+08:00")).result(timeout=1)
    assert state(db)["last_run_at"] is None


def test_two_services_cannot_run_the_same_cleanup_twice(service, db):
    create(service)
    assert service.run_one()
    age(db)
    other = pr.Service(db, service.settings, lambda: None)
    at = pr.local("2026-11-06T10:00:00+08:00")
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in (pool.submit(service.cleanup_snapshots, at), pool.submit(other.cleanup_snapshots, at)):
            result.result(timeout=5)
    assert state(db)["removed_count"] == 1


def test_generation_duration_uses_execution_time_and_survives_metadata_updates(service, db, monkeypatch):
    create(service)
    real_time = pr.time
    ticks = iter([1000, 1125.375])
    monkeypatch.setattr(pr, "time", SimpleNamespace(time=real_time.time, monotonic=lambda: next(ticks)))
    assert service.run_one()
    finished = rows(db)[0]
    assert finished["generation_seconds"] == 125.375
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_reports SET updated_at='2026-12-01T10:00:00+08:00'")
    assert service.listing("daily")["items"][0]["generation_seconds"] == 125.375
    assert service.observation()["recent"][0]["generation_seconds"] == 125.375


def test_legacy_duration_backfill_is_once_and_requires_valid_start(service, db):
    first, second = create(service), create(service)
    with closing(db()) as conn:
        conn.execute("ALTER TABLE periodic_reports DROP COLUMN generation_seconds")
        conn.execute("UPDATE periodic_reports SET status='done',updated_at='2026-10-30T12:02:05+08:00'")
        conn.execute("UPDATE periodic_reports SET started_at='2026-10-30T12:00:00+08:00' WHERE id=?", (first["id"],))
        pr.initialize(conn)
        values = dict(conn.execute("SELECT id,generation_seconds FROM periodic_reports"))
        assert values == {first["id"]: 125, second["id"]: None}
        conn.execute("UPDATE periodic_reports SET updated_at='2026-11-01T00:00:00+08:00'")
        pr.initialize(conn)
        assert dict(conn.execute("SELECT id,generation_seconds FROM periodic_reports")) == values


def test_storage_counts_utf8_and_shared_material_once(service, db):
    first, second = create(service), create(service)
    assert service.run_one() and service.run_one()
    legacy = create(service, start="2026-10-28T00:00:00+08:00", end="2026-10-29T00:00:00+08:00")
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_reports SET status='done',snapshot_json=? WHERE id=?", ('{"title":"中文资料"}',legacy["id"]))
        raw = [row[0] for row in conn.execute("SELECT snapshot_json FROM periodic_report_snapshots UNION ALL SELECT snapshot_json FROM periodic_reports WHERE snapshot_json IS NOT NULL")]
        sizes = pr.snapshot_storage(conn)
        assert sizes["bytes"] == sum(len(value.encode("utf-8")) for value in raw)
        assert sizes["bytes"] > sum(len(value) for value in raw)
        assert sizes["count"] == sizes["clearable_count"] == 2
        assert sizes["clearable_bytes"] == sizes["bytes"] and sizes["protected_bytes"] == 0


def test_manual_clear_removes_recent_material_and_keeps_reports_and_weekly_schedule(service, db):
    create(service)
    assert service.run_one()
    original = rows(db)[0]
    maintenance = state(db)
    before = service.observation()["snapshot_storage"]
    result = service.clear_snapshots(service.manual_options()["database_token"])
    assert result["removed_count"] == 1 and result["freed_bytes"] == before["bytes"]
    assert result["snapshot_storage"]["bytes"] == 0
    current = rows(db)[0]
    for key in ("result_json", "config_json", "updated_at", "generation_seconds", "snapshot_captured_at"):
        assert current[key] == original[key]
    assert state(db) == maintenance
    assert service.clear_snapshots(service.manual_options()["database_token"])["removed_count"] == 0


@pytest.mark.parametrize("status", ["pending", "running", "failed"])
def test_manual_clear_protects_incomplete_comparisons_and_reports_remaining_size(service, db, status):
    create(service)
    assert service.run_one()
    comparison = create(service)
    with closing(db()) as conn, conn:
        conn.execute("UPDATE periodic_reports SET status=? WHERE id=?", (status, comparison["id"]))
    before = service.observation()["snapshot_storage"]
    assert before["protected_bytes"] == before["bytes"] > 0
    assert before["clearable_count"] == 0
    result = service.clear_snapshots(service.manual_options()["database_token"])
    assert result["removed_count"] == result["freed_bytes"] == 0
    assert result["snapshot_storage"] == before


def test_manual_clear_rejects_stale_db_or_busy_index_and_rolls_back_failure(service, db):
    create(service)
    assert service.run_one()
    token = service.manual_options()["database_token"]
    original = rows(db)
    with pytest.raises(ValueError, match="report_database_changed"):
        service.clear_snapshots("old-db")
    with service.index_lock, ThreadPoolExecutor(max_workers=1) as pool:
        call = pool.submit(service.clear_snapshots, token)
        with pytest.raises(RuntimeError, match="report_materials_busy"): call.result(timeout=1)
    with closing(db()) as conn, conn:
        conn.execute("""CREATE TRIGGER fail_manual_clear BEFORE DELETE ON periodic_report_snapshots
            BEGIN SELECT RAISE(ABORT,'private database details'); END""")
    import sqlite3
    with pytest.raises(sqlite3.Error): service.clear_snapshots(token)
    assert rows(db) == original
    assert service.observation()["snapshot_storage"]["bytes"] > 0


def test_manual_clear_api_validates_and_returns_new_storage(service, db, monkeypatch):
    import app
    monkeypatch.setattr(app, "REPORT_SERVICE", service)
    create(service)
    assert service.run_one()
    client = app.app.test_client()
    assert client.post("/api/reports/snapshots/clear", json=[]).status_code == 400
    assert client.post("/api/reports/snapshots/clear", json={"database_token":"old"}).status_code == 409
    result = client.post("/api/reports/snapshots/clear", json={"database_token":service.manual_options()["database_token"]})
    assert result.status_code == 200 and result.json["snapshot_storage"]["bytes"] == 0
    assert result.json["freed_bytes"] > 0
