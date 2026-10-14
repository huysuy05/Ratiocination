"""``/api/projects/{id}/seeds`` (API.md "Seeds") through the real app."""

from __future__ import annotations

import re

ARXIV_API = "https://export.arxiv.org/api/query"
OA = re.compile(r"^https://api\.openalex\.org/")


def test_add_seed_by_arxiv_id_then_again(client, make_project, http_mock, load_fixture):
    project_id = make_project()
    http_mock.add("GET", ARXIV_API, load_fixture("arxiv_api_query.xml"))
    http_mock.add("GET", OA, {"error": "not found"}, status=404)

    first = client.post(
        f"/api/projects/{project_id}/seeds", json={"identifier": "https://arxiv.org/abs/1706.03762"}
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["created"] is True
    assert body["seed"]["paper"]["title"] == "Attention Is All You Need"
    assert body["seed"]["added_via"] == "arxiv" and body["seed"]["weight"] == 1.0
    assert body["enrich_job"]["kind"] == "enrich"
    assert body["enrich_job"]["params"] == {"paper_ids": [body["seed"]["paper"]["id"]]}

    again = client.post(f"/api/projects/{project_id}/seeds", json={"identifier": "arXiv:1706.03762v7"})
    assert again.status_code == 201
    assert again.json()["created"] is False and again.json()["enrich_job"] is None

    listing = client.get(f"/api/projects/{project_id}/seeds").json()
    assert [s["paper"]["identifiers"]["arxiv"] for s in listing] == ["1706.03762"]


def test_add_seed_by_paper_id(client, make_project, make_paper):
    project_id = make_project()
    paper_id = make_paper(title="Test paper A")
    response = client.post(f"/api/projects/{project_id}/seeds", json={"paper_id": paper_id, "weight": 1.5})
    assert response.status_code == 201
    seed = response.json()["seed"]
    assert seed["added_via"] == "search" and seed["weight"] == 1.5
    assert client.post(f"/api/projects/{project_id}/seeds", json={"paper_id": 999999}).status_code == 404
    both = client.post(f"/api/projects/{project_id}/seeds", json={"paper_id": paper_id, "identifier": "x"})
    assert both.status_code == 422 and both.json()["error"]["code"] == "validation_error"


def test_seed_errors_map_to_the_contract(client, make_project, http_mock):
    project_id = make_project()
    bad = client.post(f"/api/projects/{project_id}/seeds", json={"identifier": "hello world"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "unresolvable_identifier"

    http_mock.add("GET", ARXIV_API, "slow down", status=429)
    http_mock.add("GET", OA, {"error": "x"}, status=404)
    down = client.post(f"/api/projects/{project_id}/seeds", json={"identifier": "2610.12345"})
    assert down.status_code == 503 and down.json()["error"]["code"] == "source_unavailable"
    assert "http" not in down.json()["error"]["message"]

    http_mock.routes.clear()
    http_mock.add("GET", OA, {"error": "x"}, status=404)
    unknown = client.post(f"/api/projects/{project_id}/seeds", json={"identifier": "10.1234/nope"})
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "unresolvable_identifier"

    assert client.post("/api/projects/999/seeds", json={"identifier": "10.1234/x"}).status_code == 404


def test_remove_seed(client, make_project, make_paper):
    project_id = make_project()
    paper_id = make_paper()
    client.post(f"/api/projects/{project_id}/seeds", json={"paper_id": paper_id})
    assert client.delete(f"/api/projects/{project_id}/seeds/{paper_id}").status_code == 204
    assert client.get(f"/api/projects/{project_id}/seeds").json() == []
    missing = client.delete(f"/api/projects/{project_id}/seeds/{paper_id}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
