"""Identifier normalization (SPEC §5): table tests for every documented form plus property tests."""

from __future__ import annotations

import re
import string
import unicodedata

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from radar.store.identifiers import (
    normalize_doi,
    normalize_identifier,
    normalize_title,
    parse_arxiv_id,
    parse_identifier,
    surname_norm,
    version_number,
)

# Deterministic property tests: fixed seed, no example database written into the repo.
PROPERTY = settings(derandomize=True, database=None, deadline=None, max_examples=300)

# ------------------------------------------------------------------------------------------------
# normalize_doi
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.18653/v1/2020.acl-main.207", "10.18653/v1/2020.acl-main.207"),
        ("  10.18653/V1/2020.ACL-MAIN.207  ", "10.18653/v1/2020.acl-main.207"),
        ("https://doi.org/10.18653/v1/2020.acl-main.207", "10.18653/v1/2020.acl-main.207"),
        ("http://doi.org/10.18653/v1/2020.acl-main.207", "10.18653/v1/2020.acl-main.207"),
        ("https://dx.doi.org/10.18653/v1/2020.acl-main.207", "10.18653/v1/2020.acl-main.207"),
        ("http://dx.doi.org/10.18653/v1/2020.acl-main.207", "10.18653/v1/2020.acl-main.207"),
        ("HTTPS://DOI.ORG/10.18653/V1/2020.ACL-MAIN.207", "10.18653/v1/2020.acl-main.207"),
        ("doi:10.18653/v1/2020.acl-main.207", "10.18653/v1/2020.acl-main.207"),
        ("DOI: 10.18653/v1/2020.acl-main.207", "10.18653/v1/2020.acl-main.207"),
        ("doi 10.18653/v1/2020.acl-main.207", "10.18653/v1/2020.acl-main.207"),
        # trailing punctuation from pasted prose
        ("10.18653/v1/2020.acl-main.207.", "10.18653/v1/2020.acl-main.207"),
        ("10.18653/v1/2020.acl-main.207,", "10.18653/v1/2020.acl-main.207"),
        ("10.18653/v1/2020.acl-main.207;", "10.18653/v1/2020.acl-main.207"),
        ("10.18653/v1/2020.acl-main.207).", "10.18653/v1/2020.acl-main.207"),
        # the retracted Lancet paper (crossref_work_retracted.json): parentheses inside the DOI
        ("10.1016/S0140-6736(97)11096-0", "10.1016/s0140-6736(97)11096-0"),
        ("https://doi.org/10.1016/s0140-6736(97)11096-0", "10.1016/s0140-6736(97)11096-0"),
        # a balanced trailing ")" belongs to the DOI
        ("10.1234/abc(1)", "10.1234/abc(1)"),
        # arXiv DataCite DOI stays a DOI here (normalize_identifier maps it to scheme arxiv)
        ("10.48550/arXiv.2504.08385", "10.48550/arxiv.2504.08385"),
        # percent-encoded doi.org URL
        ("https://doi.org/10.1002%2Fabc.123", "10.1002/abc.123"),
        ("https://www.doi.org/10.1143/PTP.101.1155", "10.1143/ptp.101.1155"),
    ],
)
def test_normalize_doi_forms(raw: str, expected: str) -> None:
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "11.1234/abc",
        "10.12/abc",  # registrant too short
        "10.1234567890/abc",  # registrant too long
        "10.1234/",
        "10.1234/has space",
        "2504.08385",
        "https://arxiv.org/abs/2504.08385",
        "https://example.org/10.1234/abc",
        "10.1234/abc\nextra",
    ],
)
def test_normalize_doi_rejects(raw: str | None) -> None:
    assert normalize_doi(raw) is None


# ------------------------------------------------------------------------------------------------
# parse_arxiv_id
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # every example listed in SPEC §5
        ("2504.08385", ("2504.08385", None)),
        ("2504.08385v2", ("2504.08385", "v2")),
        ("arXiv:2504.08385v2", ("2504.08385", "v2")),
        ("https://arxiv.org/abs/2504.08385v2", ("2504.08385", "v2")),
        ("http://arxiv.org/pdf/2504.08385v2.pdf", ("2504.08385", "v2")),
        ("oai:arXiv.org:2504.08385v1", ("2504.08385", "v1")),
        ("10.48550/arXiv.2504.08385", ("2504.08385", None)),
        ("hep-th/9901001v2", ("hep-th/9901001", "v2")),
        ("math.GT/0309136", ("math.GT/0309136", None)),
        ("https://arxiv.org/abs/hep-th/9901001", ("hep-th/9901001", None)),
        # more real-world forms
        ("https://doi.org/10.48550/arXiv.2504.08385", ("2504.08385", None)),
        ("doi:10.48550/arxiv.2504.08385", ("2504.08385", None)),
        ("ARXIV:2004.07180", ("2004.07180", None)),  # Semantic Scholar style
        ("arXiv: 1706.03762v7", ("1706.03762", "v7")),
        ("http://arxiv.org/abs/1706.03762v7", ("1706.03762", "v7")),  # arXiv API entry id
        ("https://arxiv.org/pdf/1706.03762v7", ("1706.03762", "v7")),
        ("https://export.arxiv.org/abs/2504.08385", ("2504.08385", None)),
        ("https://arxiv.org/abs/2504.08385v2/", ("2504.08385", "v2")),
        ("https://arxiv.org/abs/2504.08385?context=cs", ("2504.08385", None)),
        ("https://arxiv.org/html/2504.08385v2", ("2504.08385", "v2")),
        ("oai:arXiv.org:2610.12545v1", ("2610.12545", "v1")),  # arxiv_feed_multi.xml entry id
        ("0704.0001", ("0704.0001", None)),  # first new-style id (4-digit sequence)
        ("2504.08385V3", ("2504.08385", "v3")),
        # old style: archive lowercased, subject class case kept
        ("HEP-TH/9901001", ("hep-th/9901001", None)),
        ("http://arxiv.org/abs/hep-th/9901001v3", ("hep-th/9901001", "v3")),  # arxiv_api_query.xml
        ("math.GT/0309136v1", ("math.GT/0309136", "v1")),
        ("cond-mat/0102536", ("cond-mat/0102536", None)),
        ("arXiv:math.GT/0309136", ("math.GT/0309136", None)),
        ("https://arxiv.org/pdf/hep-th/9901001v3.pdf", ("hep-th/9901001", "v3")),
    ],
)
def test_parse_arxiv_id_forms(raw: str, expected: tuple[str, str | None]) -> None:
    assert parse_arxiv_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "2504.083",  # sequence too short
        "2504.083850",  # sequence too long
        "25040.08385",
        "hep-th/990100",  # old style needs 7 digits
        "math.GTX/0309136",  # subject class is two letters
        "10.1234/2504.08385",  # an ordinary DOI is not an arXiv id
        "https://example.org/abs/2504.08385",
        "W4414828500",
        "2504.08385v",
        "see 2504.08385 here",
    ],
)
def test_parse_arxiv_id_rejects(raw: str | None) -> None:
    assert parse_arxiv_id(raw) is None


# ------------------------------------------------------------------------------------------------
# normalize_identifier / parse_identifier
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scheme", "raw", "expected"),
    [
        ("doi", "https://doi.org/10.18653/v1/2020.acl-main.207", ("doi", "10.18653/v1/2020.acl-main.207")),
        ("DOI", "10.1016/S0140-6736(97)11096-0", ("doi", "10.1016/s0140-6736(97)11096-0")),
        # arXiv DOIs are stored only as scheme arxiv (openalex_work_arxiv.json, s2_batch.json)
        ("doi", "https://doi.org/10.48550/arxiv.2504.08385", ("arxiv", "2504.08385")),
        ("doi", "10.48550/arXiv.2504.08385", ("arxiv", "2504.08385")),
        ("doi", "10.48550/arXiv.2504.08385v2", ("arxiv", "2504.08385")),
        ("doi", "10.48550/arxiv.not-an-id", None),
        ("arxiv", "2504.08385v2", ("arxiv", "2504.08385")),
        ("arxiv", "oai:arXiv.org:2405.01930v3", ("arxiv", "2405.01930")),
        ("ArXiv", "2004.07180", ("arxiv", "2004.07180")),
        ("arxiv", "hep-th/9901001v3", ("arxiv", "hep-th/9901001")),
        ("arxiv", "10.48550/arXiv.2504.08385", ("arxiv", "2504.08385")),
        # OpenAlex URL forms
        ("openalex", "https://openalex.org/W4414828500", ("openalex", "W4414828500")),
        ("openalex", "http://openalex.org/W3016913119", ("openalex", "W3016913119")),
        ("openalex", "https://api.openalex.org/works/W3016913119", ("openalex", "W3016913119")),
        ("openalex", "openalex.org/w2117847125", ("openalex", "W2117847125")),
        ("openalex", "w2117847125", ("openalex", "W2117847125")),
        ("openalex", "T10203", None),
        ("openalex", "https://openalex.org/A5023888391", None),
        # S2 hex paper ids (s2_batch.json); CorpusId is never stored
        (
            "s2",
            "a3e4ceb42cbcd2c807d53aff90a8cb1f5ee3f031",
            ("s2", "a3e4ceb42cbcd2c807d53aff90a8cb1f5ee3f031"),
        ),
        (
            "s2",
            "A3E4CEB42CBCD2C807D53AFF90A8CB1F5EE3F031",
            ("s2", "a3e4ceb42cbcd2c807d53aff90a8cb1f5ee3f031"),
        ),
        (
            "s2",
            "https://www.semanticscholar.org/paper/SPECTER/a3e4ceb42cbcd2c807d53aff90a8cb1f5ee3f031",
            ("s2", "a3e4ceb42cbcd2c807d53aff90a8cb1f5ee3f031"),
        ),
        ("s2", "215768677", None),
        ("s2", "CorpusId:215768677", None),
        ("s2", "a3e4ceb42cbcd2c807d53aff90a8cb1f5ee3f03", None),  # 39 chars
        # PMID (openalex_work_retracted.json ids.pmid)
        ("pmid", "https://pubmed.ncbi.nlm.nih.gov/9500320", ("pmid", "9500320")),
        ("pmid", "PMID: 9500320", ("pmid", "9500320")),
        ("pmid", "9500320", ("pmid", "9500320")),
        ("pmid", "0", None),
        ("mag", "3016913119", None),
        ("doi", None, None),
        ("doi", "", None),
    ],
)
def test_normalize_identifier(scheme: str, raw: str | None, expected: tuple[str, str] | None) -> None:
    assert normalize_identifier(scheme, raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.18653/v1/2020.acl-main.207", ("doi", "10.18653/v1/2020.acl-main.207")),
        ("https://doi.org/10.18653/v1/2020.acl-main.207", ("doi", "10.18653/v1/2020.acl-main.207")),
        ("doi:10.1143/PTP.101.1155", ("doi", "10.1143/ptp.101.1155")),
        ("https://doi.org/10.48550/arXiv.2504.08385", ("arxiv", "2504.08385")),
        ("10.48550/arXiv.2504.08385", ("arxiv", "2504.08385")),
        ("2504.08385", ("arxiv", "2504.08385")),
        ("arXiv:2504.08385v2", ("arxiv", "2504.08385")),
        ("https://arxiv.org/abs/2504.08385v2", ("arxiv", "2504.08385")),
        ("http://arxiv.org/pdf/2504.08385v2.pdf", ("arxiv", "2504.08385")),
        ("hep-th/9901001", ("arxiv", "hep-th/9901001")),
        ("https://arxiv.org/abs/hep-th/9901001", ("arxiv", "hep-th/9901001")),
        ("math.GT/0309136", ("arxiv", "math.GT/0309136")),
        ("W4414828500", ("openalex", "W4414828500")),
        ("https://openalex.org/W4414828500", ("openalex", "W4414828500")),
        ("https://api.openalex.org/works/W3016913119", ("openalex", "W3016913119")),
        ("  https://openalex.org/W3016913119  ", ("openalex", "W3016913119")),
    ],
)
def test_parse_identifier(raw: str, expected: tuple[str, str]) -> None:
    assert parse_identifier(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "Attention is all you need",
        "a3e4ceb42cbcd2c807d53aff90a8cb1f5ee3f031",
        "9500320",
        "T10203",
    ],
)
def test_parse_identifier_rejects(raw: str | None) -> None:
    assert parse_identifier(raw) is None


# ------------------------------------------------------------------------------------------------
# version_number, normalize_title, surname_norm
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("v1", 1),
        ("v2", 2),
        ("v12", 12),
        ("V3", 3),
        ("published", None),
        (None, None),
        ("", None),
        ("2", None),
    ],
)
def test_version_number(label: str | None, expected: int | None) -> None:
    assert version_number(label) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Attention Is All You Need", "attention is all you need"),
        (
            "SPECTER: Document-level Representation Learning using Citation-informed Transformers",
            "specter document level representation learning using citation informed transformers",
        ),
        ("Test paper A: Ranking {Synthetic} Things", "test paper a ranking synthetic things"),
        ("Scaling to $10^{-3}$ loss", "scaling to 10 3 loss"),
        ("A \\textit{bold}   claim", "a textitbold claim"),
        ("ＦＵＬＬＷＩＤＴＨ Title", "fullwidth title"),  # NFKC
        ("Straße und Maße", "strasse und masse"),  # casefold
        ("Über   die   Natur", "über die natur"),  # Unicode letters are kept
        ("深度学习：综述", "深度学习 综述"),
        ("snake_case and kebab-case", "snake case and kebab case"),
        ("", ""),
        ("  ---  ", ""),
    ],
)
def test_normalize_title(title: str, expected: str) -> None:
    assert normalize_title(title) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Ashish Vaswani", "vaswani"),
        ("Iyengar, Madhav", "iyengar"),  # OpenAlex "Last, First"
        ("Madhav Iyengar", "iyengar"),
        ("Daniel S. Weld", "weld"),
        ("JA Walker-Smith", "walker-smith"),
        ("SIMON H MURCH", "murch"),
        ("José Martínez", "martinez"),
        ("Müller, Jürgen", "muller"),
        ("Fabien Furfaro", "furfaro"),
        ("Plato", "plato"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_surname_norm(name: str, expected: str) -> None:
    assert surname_norm(name) == expected


# ------------------------------------------------------------------------------------------------
# Properties
# ------------------------------------------------------------------------------------------------

_doi_suffix = st.text(
    alphabet=string.ascii_letters + string.digits + "-._;()/:<>[]", min_size=1, max_size=30
).filter(lambda s: s[-1] not in ".,;)" and s.count("(") == s.count(")"))
_doi = st.builds(
    lambda registrant, suffix: f"10.{registrant}/{suffix}",
    st.integers(min_value=1000, max_value=999_999_999),
    _doi_suffix,
)
_doi_prefix = st.sampled_from(
    [
        "",
        "doi:",
        "DOI: ",
        "doi ",
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
    ]
)
_new_style_base = st.builds(
    lambda yymm, seq, width: f"{yymm:04d}.{seq:0{width}d}",
    st.integers(min_value=0, max_value=9999),
    st.integers(min_value=0, max_value=9999),
    st.sampled_from([4, 5]),
)
_version = st.one_of(st.none(), st.integers(min_value=1, max_value=99).map(lambda n: f"v{n}"))
_arxiv_form = st.sampled_from(
    [
        "{id}",
        "arXiv:{id}",
        "https://arxiv.org/abs/{id}",
        "http://arxiv.org/pdf/{id}.pdf",
        "oai:arXiv.org:{id}",
        "https://export.arxiv.org/abs/{id}",
    ]
)


@PROPERTY
@given(doi=_doi, prefix=_doi_prefix, trailing=st.sampled_from(["", ".", ",", ";", ").", " "]))
def test_property_doi_forms_normalize_to_lowercase_bare_doi(doi: str, prefix: str, trailing: str) -> None:
    expected = doi.lower()
    raw = f"{prefix}{doi}{trailing}"
    assert normalize_doi(raw) == expected
    assert normalize_doi(raw.upper()) == expected
    assert re.fullmatch(r"10\.\d{4,9}/\S+", expected)


@PROPERTY
@given(st.text())
def test_property_normalize_doi_total_and_idempotent(text: str) -> None:
    result = normalize_doi(text)
    if result is not None:
        assert result == result.lower()
        assert normalize_doi(result) == result
        assert re.fullmatch(r"10\.\d{4,9}/\S+", result)


@PROPERTY
@given(base=_new_style_base, version=_version, form=_arxiv_form)
def test_property_new_style_arxiv_round_trip(base: str, version: str | None, form: str) -> None:
    raw = form.format(id=f"{base}{version or ''}")
    assert parse_arxiv_id(raw) == (base, version)
    assert normalize_identifier("arxiv", raw) == ("arxiv", base)
    assert parse_identifier(raw) == ("arxiv", base)
    # the same work through its DataCite DOI
    assert normalize_identifier("doi", f"https://doi.org/10.48550/arXiv.{base}") == ("arxiv", base)


@PROPERTY
@given(
    archive=st.sampled_from(["hep-th", "hep-ph", "math", "cs", "cond-mat", "astro-ph", "q-bio", "math-ph"]),
    subject=st.one_of(st.none(), st.sampled_from([".GT", ".AG", ".LG", ".AI"])),
    number=st.integers(min_value=0, max_value=9_999_999),
    version=_version,
    upper=st.booleans(),
)
def test_property_old_style_arxiv(
    archive: str, subject: str | None, number: int, version: str | None, upper: bool
) -> None:
    base = f"{archive}{subject or ''}/{number:07d}"
    raw_archive = archive.upper() if upper else archive
    raw = f"https://arxiv.org/abs/{raw_archive}{subject or ''}/{number:07d}{version or ''}"
    assert parse_arxiv_id(raw) == (base, version)


@PROPERTY
@given(
    digits=st.integers(min_value=1, max_value=10**11),
    form=st.sampled_from(
        ["W{d}", "w{d}", "https://openalex.org/W{d}", "https://api.openalex.org/works/W{d}"]
    ),
)
def test_property_openalex_forms(digits: int, form: str) -> None:
    raw = form.format(d=digits)
    assert normalize_identifier("openalex", raw) == ("openalex", f"W{digits}")
    assert parse_identifier(raw) == ("openalex", f"W{digits}")


@PROPERTY
@given(st.text(alphabet="0123456789abcdefABCDEF", min_size=40, max_size=40))
def test_property_s2_hex_ids(hex_id: str) -> None:
    assert normalize_identifier("s2", hex_id) == ("s2", hex_id.lower())


@PROPERTY
@given(scheme=st.sampled_from(["doi", "arxiv", "openalex", "s2", "pmid", "other"]), text=st.text())
def test_property_normalize_identifier_is_idempotent(scheme: str, text: str) -> None:
    result = normalize_identifier(scheme, text)
    if result is not None:
        assert result[0] in {"doi", "arxiv", "openalex", "s2", "pmid"}
        assert normalize_identifier(*result) == result
    parsed = parse_identifier(text)
    if parsed is not None:
        assert normalize_identifier(*parsed) == parsed


@PROPERTY
@given(st.text())
def test_property_normalize_title_is_canonical(text: str) -> None:
    norm = normalize_title(text)
    assert normalize_title(norm) == norm
    assert norm == norm.strip() and "  " not in norm
    assert all(ch == " " or ch.isalnum() for ch in norm)


@PROPERTY
@given(
    words=st.lists(
        st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=8), min_size=1, max_size=6
    ),
    noise=st.sampled_from([" ", "  ", " - ", ": ", " {", "} ", " $", " \\ "]),
)
def test_property_normalize_title_ignores_case_and_punctuation(words: list[str], noise: str) -> None:
    plain = " ".join(words)
    noisy = noise.join(w.upper() for w in words)
    assert normalize_title(noisy) == normalize_title(plain) == plain.lower()


@PROPERTY
@given(
    first=st.text(alphabet=string.ascii_letters, min_size=1, max_size=10),
    last=st.text(alphabet=string.ascii_letters + "éüøà", min_size=1, max_size=12),
)
def test_property_surname_forms_agree(first: str, last: str) -> None:
    expected = surname_norm(last)
    assert surname_norm(f"{first} {last}") == expected
    assert surname_norm(f"{last}, {first}") == expected
    assert expected == expected.casefold()
    assert not any(unicodedata.combining(ch) for ch in expected)
    assert "é" not in expected and "ü" not in expected and "à" not in expected


@PROPERTY
@given(st.text())
def test_property_functions_never_raise(text: str) -> None:
    normalize_doi(text)
    parse_arxiv_id(text)
    parse_identifier(text)
    version_number(text)
    surname_norm(text)
    for scheme in ("doi", "arxiv", "openalex", "s2", "pmid"):
        normalize_identifier(scheme, text)
