from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from preauth.infrastructure.db.models import APPEND_ONLY_TABLES, Base
from preauth.infrastructure.db.session import build_engine
from tests.conftest import alembic_config


def test_migrations_match_orm_metadata(engine):
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_upgrade_downgrade_upgrade_round_trip(tmp_path):
    url = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    eng = build_engine(url)
    assert set(inspect(eng).get_table_names()) == {"alembic_version"}
    eng.dispose()
    command.upgrade(cfg, "head")


def test_voice_reliability_upgrade_preserves_existing_cases_and_audit(tmp_path):
    url = f"sqlite:///{tmp_path / 'upgrade.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0001")
    eng = build_engine(url)
    with eng.begin() as conn:
        _insert_case_and_event(conn)
    command.upgrade(cfg, "head")
    with eng.connect() as conn:
        assert conn.execute(text("SELECT case_reference FROM pre_authorization_cases")).scalar_one() == "PA-TEST0001"
        assert conn.execute(text("SELECT event_type FROM audit_events")).scalar_one() == "CASE_CREATED"
        assert conn.execute(text("SELECT count(*) FROM voice_tool_executions")).scalar_one() == 0
    eng.dispose()


def _trigger_names(conn) -> set[str]:
    if conn.dialect.name == "postgresql":
        return set(conn.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")).scalars())
    return set(conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'trigger'")).scalars())


def _insert_case_and_event(conn):
    now = datetime.now(UTC).isoformat()
    conn.execute(
        text(
            "INSERT INTO pre_authorization_cases (id, case_reference, status, created_by_actor_type, "
            "created_by_actor_id, created_at, updated_at, version) "
            "VALUES ('c1', 'PA-TEST0001', 'RECEIVED', 'SYSTEM', 'sys', :now, :now, 1)"
        ),
        {"now": now},
    )
    conn.execute(
        text(
            "INSERT INTO audit_events (id, case_id, sequence, event_type, actor_type, occurred_at, data) "
            "VALUES ('e1', 'c1', 1, 'CASE_CREATED', 'SYSTEM', :now, '{}')"
        ),
        {"now": now},
    )


def test_audit_events_cannot_be_updated_or_deleted(engine):
    with engine.begin() as conn:
        _insert_case_and_event(conn)
    with pytest.raises(IntegrityError, match="append-only"), engine.begin() as conn:
        conn.execute(text("UPDATE audit_events SET event_type = 'CASE_CLOSED' WHERE id = 'e1'"))
    with pytest.raises(IntegrityError, match="append-only"), engine.begin() as conn:
        conn.execute(text("DELETE FROM audit_events WHERE id = 'e1'"))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT event_type FROM audit_events")).scalar_one() == "CASE_CREATED"


def test_every_append_only_table_has_triggers(engine):
    with engine.connect() as conn:
        triggers = _trigger_names(conn)
        postgres = conn.dialect.name == "postgresql"
    for table in APPEND_ONLY_TABLES:
        if postgres:
            assert f"trg_{table}_append_only" in triggers
        else:
            assert f"trg_{table}_no_update" in triggers
            assert f"trg_{table}_no_delete" in triggers


def test_review_decision_status_must_match_decision_type(engine):
    with engine.begin() as conn:
        _insert_case_and_event(conn)
    with pytest.raises(IntegrityError, match="decision_matches_status"), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO review_decisions (id, case_id, sequence, recommendation_id, decision, rationale, "
                "reviewer_id, reviewer_role, is_override, from_status, to_status, decided_at) VALUES "
                "('d1', 'c1', 1, 'r-missing', 'DENY', 'x', 'rev', 'CLINICAL_REVIEWER', :false, "
                "'PENDING_HUMAN_REVIEW', 'APPROVED', :now)"
            ),
            {"false": False, "now": datetime.now(UTC)},
        )


def test_invalid_enum_value_rejected_by_database(engine):
    with pytest.raises(IntegrityError, match="casestatus"), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO pre_authorization_cases (id, case_reference, status, created_by_actor_type, "
                "created_by_actor_id, created_at, updated_at, version) "
                "VALUES ('c2', 'PA-TEST0002', 'AUTO_APPROVED', 'SYSTEM', 'sys', :now, :now, 1)"
            ),
            {"now": datetime.now(UTC)},
        )


def test_voice_channel_audit_event_types_are_accepted(engine):
    with engine.begin() as conn:
        _insert_case_and_event(conn)
        conn.execute(
            text(
                "INSERT INTO audit_events (id, case_id, sequence, event_type, actor_type, occurred_at, data) "
                "VALUES ('e2', 'c1', 2, 'CALL_RECORDED', 'SYSTEM', :now, '{}')"
            ),
            {"now": datetime.now(UTC)},
        )
    with pytest.raises(IntegrityError, match="auditeventtype"), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_events (id, case_id, sequence, event_type, actor_type, occurred_at, data) "
                "VALUES ('e3', 'c1', 3, 'AUTO_APPROVED_BY_AGENT', 'SYSTEM', :now, '{}')"
            ),
            {"now": datetime.now(UTC)},
        )
