"""arXiv OAI-PMH (arXivRaw) connector against the real 2026-10-12 page fixture."""

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest

from radar.connectors.arxiv_oai import OAI_URL, harvest, parse_oai_page, set_for_category
from radar.net.http import SourceUnavailable

NO_RECORDS = b"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><responseDate>2026-10-12T00:00:00Z</responseDate>
<request verb="ListRecords">http://oaipmh.arxiv.org/oai</request>
<error code="noRecordsMatch">No records</error></OAI-PMH>"""
BAD_ARGUMENT = NO_RECORDS.replace(b'code="noRecordsMatch"', b'code="badArgument"')


def test_parse_page_reads_versions_dates_and_identifiers(load_fixture):
    page = parse_oai_page(load_fixture("arxiv_oai_raw_page.xml"))
    assert page.raw_count == 4 and len(page.records) == 4
    assert page.resumption_token.startswith("verb%3DListRecords")
    by_id = {r.identifiers["arxiv"]: r for r in page.records}
    multi = by_id["2601.00765"]
    assert multi.source == "arxiv_oai"
    assert multi.version_label == "v2" and multi.source_id == "2601.00765v2"
    assert multi.publication_date == date(2026, 1, 2)  # v1 date, not the v2 date
    assert multi.source_updated_at.isoformat() == "2026-10-08T18:13:48+00:00"
    assert multi.authors == ["Joslyn Orgill", "Andra Rice", "Max Fowler", "Seth Poulsen"]
    assert multi.pdf_url == "https://arxiv.org/pdf/2601.00765v2"
    cross = by_id["2610.11099"]
    assert cross.categories == ["cs.SD", "eess.AS"] and cross.primary_category == "cs.SD"
    assert cross.identifiers["doi"] == "10.21437/Interspeech.2026-1799"
    assert cross.venue == "Proc. Interspeech 2026, 6958-6962"
    assert cross.license == "http://creativecommons.org/licenses/by-nc-sa/4.0/"
    assert cross.abstract.startswith("Speaker verification")


def test_no_records_match_is_an_empty_page():
    page = parse_oai_page(NO_RECORDS)
    assert page.records == [] and page.resumption_token is None


def test_protocol_errors_raise():
    with pytest.raises(ValueError, match="badArgument"):
        parse_oai_page(BAD_ARGUMENT)


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("cs.IR", "cs"),
        ("math.GT", "math"),
        ("stat.ML", "stat"),
        ("eess.AS", "eess"),
        ("q-fin.ST", "q-fin"),
        ("hep-th", "physics:hep-th"),
        ("quant-ph", "physics:quant-ph"),
        ("physics.optics", "physics:physics"),
    ],
)
def test_set_for_category(category, expected):
    assert set_for_category(category) == expected


def test_harvest_follows_resumption_tokens(http_mock, load_fixture):
    first = load_fixture("arxiv_oai_raw_page.xml")
    last = first.split(b"<resumptionToken")[0] + b"</ListRecords>\n</OAI-PMH>\n"
    pages = iter([first, last])
    http_mock.add("GET", OAI_URL, lambda request: next(pages))
    got = list(harvest("cs", date(2026, 10, 1), date(2026, 10, 12)))
    assert [len(p.records) for p in got] == [4, 4]
    q1 = parse_qs(urlsplit(str(http_mock.requests[0].url)).query)
    assert q1 == {
        "verb": ["ListRecords"],
        "metadataPrefix": ["arXivRaw"],
        "set": ["cs"],
        "from": ["2026-10-01"],
        "until": ["2026-10-12"],
    }
    q2 = parse_qs(urlsplit(str(http_mock.requests[1].url)).query)
    assert set(q2) == {"verb", "resumptionToken"}  # a token request carries nothing else
    assert q2["resumptionToken"][0].startswith("verb%3DListRecords")  # sent back verbatim


def test_harvest_stops_at_the_page_cap(http_mock, load_fixture):
    http_mock.add("GET", OAI_URL, load_fixture("arxiv_oai_raw_page.xml"))  # always has a token
    got = list(harvest("cs", date(2026, 10, 1), date(2026, 10, 12), max_pages=3))
    assert len(got) == 3 and len(http_mock.requests) == 3


def test_harvest_non_200_raises(http_mock):
    http_mock.add("GET", OAI_URL, "busy", status=404)
    with pytest.raises(SourceUnavailable):
        list(harvest("cs", date(2026, 10, 1), date(2026, 10, 12)))
