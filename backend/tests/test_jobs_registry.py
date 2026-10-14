"""Job registry (SPEC §8): lazy "module:function" targets, test overrides, parameter validation."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import pytest

from radar.jobs import registry
from radar.schemas import JobKind

BACKEND_DIR = Path(__file__).resolve().parents[1]

JOB_MODULES = (
    "radar.jobs.pipeline",
    "radar.ingestion.feeds",
    "radar.ingestion.backfill",
    "radar.ingestion.enrich",
    "radar.ingestion.vectors",
    "radar.graph.expand",
    "radar.documents.service",
    "radar.embeddings.registry",
)


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    yield
    registry.reset_overrides()


def test_registry_covers_every_api_job_kind() -> None:
    assert set(registry.REGISTRY) == set(get_args(JobKind))
    assert registry.REGISTRY["build_deck"] == "radar.jobs.pipeline:run_build_deck"
    assert registry.REGISTRY["daily"] == "radar.jobs.pipeline:run_daily"
    assert registry.REGISTRY["embed"] == "radar.ingestion.vectors:run_embed_papers"
    assert registry.PROJECT_REQUIRED == frozenset({"backfill", "build_deck"})
    assert registry.known_kinds() == sorted(registry.REGISTRY)


def test_importing_the_jobs_package_imports_no_job_module() -> None:
    code = (
        "import json, sys\n"
        "import radar.jobs, radar.jobs.registry, radar.jobs.runner, radar.jobs.triggers, radar.jobs.scheduler\n"
        "import radar.api.jobs, radar.cli\n"
        f"mods = {list(JOB_MODULES)!r} + ['apscheduler', 'radar.ranking', 'radar.connectors']\n"
        "print(json.dumps(sorted(m for m in mods if m in sys.modules)))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []


@pytest.mark.parametrize("kind", sorted(registry.REGISTRY))
def test_every_target_resolves_to_its_function(kind: str) -> None:
    module_name, attr = registry.REGISTRY[kind].split(":")
    fn = registry.resolve(kind)
    assert fn is getattr(importlib.import_module(module_name), attr)
    assert callable(fn)


def test_unknown_kind() -> None:
    assert not registry.is_known("no_such_kind")
    assert not registry.is_known(None)
    with pytest.raises(KeyError) as info:
        registry.resolve("no_such_kind")
    assert isinstance(info.value, registry.UnknownJobKind)
    assert "no_such_kind" in str(info.value)
    with pytest.raises(KeyError):
        registry.target("no_such_kind")


def test_register_overrides_and_unregister_restores() -> None:
    def fake(ctx: Any) -> dict[str, Any]:
        return {}

    registry.register("expand", fake)
    assert registry.resolve("expand") is fake
    registry.unregister("expand")
    from radar.graph.expand import run_expand

    assert registry.resolve("expand") is run_expand

    registry.register("test_new_kind", fake)
    assert registry.is_known("test_new_kind")
    assert "test_new_kind" in registry.known_kinds()
    registry.reset_overrides()
    assert not registry.is_known("test_new_kind")


def test_register_with_a_string_target_is_lazy() -> None:
    registry.register("test_string", "radar.jobs.pipeline:run_daily")
    assert registry.target("test_string") == "radar.jobs.pipeline:run_daily"
    from radar.jobs.pipeline import run_daily

    assert registry.resolve("test_string") is run_daily
    registry.register("test_missing", "radar.no_such_module:fn")
    with pytest.raises(ImportError):
        registry.resolve("test_missing")
    registry.register("test_missing_attr", "radar.jobs.pipeline:no_such_function")
    with pytest.raises(AttributeError):
        registry.resolve("test_missing_attr")


def test_register_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        registry.register("", lambda ctx: None)
    with pytest.raises(ValueError):
        registry.register("x" * 33, lambda ctx: None)
    with pytest.raises(ValueError):
        registry.register("test_bad", "no_colon_here")
    with pytest.raises(TypeError):
        registry.register("test_bad", 42)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------------
# validate_params
# ------------------------------------------------------------------------------------------------


def _paths(kind: str, params: dict[str, Any]) -> list[str]:
    with pytest.raises(registry.JobParamsError) as info:
        registry.validate_params(kind, params)
    return [f["path"] for f in info.value.fields]


def test_validate_params_accepts_valid_parameters() -> None:
    registry.validate_params("enrich", {})
    registry.validate_params("enrich", {"paper_ids": [1, 2], "limit": 10})
    registry.validate_params("enrich", {"paper_ids": None})
    registry.validate_params("expand", {"paper_id": 5, "force": True, "auto": False})
    registry.validate_params("build_deck", {"mode": "initial"})
    registry.validate_params("download_model", {"model_id": None})
    registry.validate_params("process_document", {"document_id": 3})
    registry.validate_params("daily", {})


def test_validate_params_rejects_unknown_missing_and_mistyped() -> None:
    assert _paths("enrich", {"bogus": 1}) == ["params.bogus"]
    assert _paths("enrich", {"project_id": 1}) == ["params.project_id"]
    assert _paths("expand", {}) == ["params.paper_id"]
    assert _paths("enrich", {"limit": "10"}) == ["params.limit"]  # strict: no coercion
    assert _paths("expand", {"paper_id": True}) == ["params.paper_id"]
    assert _paths("download_model", {"model_id": 5}) == ["params.model_id"]
    paths = _paths("enrich", {"paper_ids": [1, "zqcanary"]})
    assert paths and all(p.startswith("params.paper_ids") for p in paths)


def test_validate_params_messages_never_echo_input() -> None:
    with pytest.raises(registry.JobParamsError) as info:
        registry.validate_params("enrich", {"limit": "zqcanary-limit", "zqcanary-key": 1})
    assert "zqcanary" not in json.dumps([f["message"] for f in info.value.fields])


def test_validate_params_with_fakes() -> None:
    registry.register("test_kwargs", lambda ctx, **kw: kw)
    registry.validate_params("test_kwargs", {"anything": [1, {"x": None}]})

    def untyped(ctx, a, b=2):  # type: ignore[no-untyped-def]
        return {}

    registry.register("test_untyped", untyped)
    registry.validate_params("test_untyped", {"a": "any value"})
    with pytest.raises(registry.JobParamsError):
        registry.validate_params("test_untyped", {"b": 1})  # a is required

    def unresolvable(ctx: Any, a: NoSuchType) -> None:  # type: ignore[name-defined]  # noqa: F821
        return None

    registry.register("test_unresolvable", unresolvable)
    registry.validate_params("test_unresolvable", {"a": object()})  # annotation unknown -> any value

    registry.register("test_broken_module", "radar.no_such_module:fn")
    registry.validate_params("test_broken_module", {"x": 1})  # not importable: the job will fail instead

    with pytest.raises(KeyError):
        registry.validate_params("no_such_kind", {})
