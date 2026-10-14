# Test fixtures

Real public metadata fetched on 2026-10-12, used to test parsers against today's actual formats.
Do not edit by hand except to trim; add new files instead.

| file | source request | notes |
|---|---|---|
| `arxiv_feed_multi.xml` | `https://rss.arxiv.org/atom/cs.IR+cs.DL+cs.CL` | trimmed to 7 entries: 2 `new`, 2 `cross`, 2 `replace`, 1 `replace-cross` |
| `arxiv_feed_empty.xml` | same feed header, no entries | shape of a weekend/holiday feed |
| `arxiv_api_query.xml` | `https://export.arxiv.org/api/query?id_list=2504.08385,1706.03762v7,hep-th/9901001` | includes an old-style id |
| `arxiv_oai_raw_page.xml` | `https://oaipmh.arxiv.org/oai?verb=ListRecords&metadataPrefix=arXivRaw&set=cs&from=2026-10-12&until=2026-10-12` | trimmed to 4 records (multi-version, cross-listed with DOI + journal-ref, old papers updated today) plus the resumption token |
| `openalex_work_arxiv.json` | `GET /works/doi:10.48550/arxiv.2504.08385` | arXiv DataCite DOI: topics, keywords, OA; 0 referenced_works |
| `openalex_work_published.json` | `GET /works/doi:10.18653/v1/2020.acl-main.207` | SPECTER (ACL 2020): 54 referenced_works |
| `openalex_work_retracted.json` | `GET /works/doi:10.1016/s0140-6736(97)11096-0` | `is_retracted: true`, no abstract |
| `openalex_list_cites.json` | `GET /works?filter=cites:W3016913119&per-page=3&select=…` | list call; `meta.cost_usd = 0.0001` |
| `openalex_authors.json` | `GET /authors?filter=openalex:A5064858748\|A5000339878\|A5090038537\|A5043450042\|A5085011940&select=id,display_name,orcid,works_count,cited_by_count,summary_stats,last_known_institutions` | the five SPECTER authors; list call, `meta.cost_usd = 0.0001` |
| `openalex_author_works.json` | `GET /works?filter=author.id:A5064858748&sort=cited_by_count:desc&per-page=3&select=…,referenced_works` | Arman Cohan's three most-cited works (SciBERT, Longformer, …); abstracts trimmed to 40 words |
| `s2_references.json` | `GET /graph/v1/paper/ARXIV:2004.07180/references?limit=3&fields=contexts,intents,isInfluential,paperId,externalIds,title,year,abstract,authors,venue` | entries have `citedPaper`, `contexts`, `intents`, `isInfluential`; top-level `citingPaperInfo` |
| `s2_citations.json` | same for `/citations` | entries have `citingPaper` |
| `s2_batch.json` | `POST /graph/v1/paper/batch` with ids `ARXIV:2004.07180`, `ARXIV:2504.08385`, `DOI:10.0000/does-not-exist` | unknown ids come back as `null` |

OpenAlex response headers seen on 2026-10-12 without an API key: `x-ratelimit-limit: 1000`,
`x-ratelimit-limit-usd: 0.1`, `x-ratelimit-remaining-usd`, `x-ratelimit-cost-usd: 0` for a
single-work lookup, `x-ratelimit-reset` (seconds). Semantic Scholar without a key intermittently
returns HTTP 429.
