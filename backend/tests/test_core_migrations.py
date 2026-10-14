"""Alembic migrations match the ORM models (SPEC §4)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from radar import db
from radar.config import BACKEND_DIR, MIGRATIONS_DIR
from radar.models import Base


def _diff(url: str) -> list[object]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"compare_type": True, "render_as_batch": True})
            return list(compare_metadata(context, Base.metadata))
    finally:
        engine.dispose()


def test_fresh_upgrade_head_matches_models(tmp_path: Path) -> None:
    """``alembic upgrade head`` from backend/ (alembic.ini + env.py; URL from RADAR_DATABASE_URL)."""
    db_file = tmp_path / "migrated.db"
    url = f"sqlite:///{db_file}"
    env = {**os.environ, "RADAR_DATABASE_URL": url, "RADAR_DATA_DIR": str(tmp_path)}
    alembic_bin = Path(sys.executable).with_name("alembic")
    cmd = (
        [str(alembic_bin), "upgrade", "head"]
        if alembic_bin.exists()
        else [sys.executable, "-m", "alembic", "upgrade", "head"]
    )
    out = subprocess.run(cmd, cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert _diff(url) == []
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == "0002_project_keywords"
            )
    finally:
        engine.dispose()


def test_app_database_matches_models(radar_data_dir: Path) -> None:
    # The per-test DB was created by radar.db.init_db (programmatic upgrade head).
    assert _diff(f"sqlite:///{radar_data_dir / 'radar.db'}") == []


def test_partial_unique_index_on_active_jobs() -> None:
    with db.session_scope() as s:
        sql = s.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'uq_jobs_active'")
        ).scalar()
    assert sql is not None
    normalized = " ".join(sql.split()).upper()
    assert "UNIQUE" in normalized
    assert "WHERE STATUS IN ('QUEUED', 'RUNNING')" in normalized


def test_downgrade_and_upgrade_again(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    engine = create_engine(url)
    try:
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    finally:
        engine.dispose()
    command.upgrade(cfg, "head")
    assert _diff(url) == []


def test_single_head_revision() -> None:
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["0002_project_keywords"]


def test_keywords_migration_gives_existing_projects_an_empty_list(tmp_path: Path) -> None:
    """0002 adds ``projects.keywords_json`` with ``[]`` for projects created before it; downgrade drops it."""
    url = f"sqlite:///{tmp_path / 'keywords.db'}"
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0001_initial")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, email_verified, display_name, is_admin, created_at, preferences_json) VALUES (1, 0, 'You', 0, '2026-10-13 00:00:00', '{}')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO projects (id, owner_id, name, research_question, description, settings_json, active, created_at, updated_at) VALUES (1, 1, 'Test project', 'Test question?', '', '{}', 1, '2026-10-13 00:00:00', '2026-10-13 00:00:00')"
                )
            )
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT keywords_json FROM projects WHERE id = 1")).scalar() == "[]"
        command.downgrade(cfg, "0001_initial")
        assert "keywords_json" not in {c["name"] for c in inspect(engine).get_columns("projects")}
    finally:
        engine.dispose()
