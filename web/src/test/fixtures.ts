/**
 * Synthetic API objects for tests. Everything is obviously fake ("Test paper A") and shaped exactly
 * like docs/API.md v0.3; pass overrides for the fields a test cares about.
 */
import type {
  AuthConfig,
  DashboardActivity,
  DashboardFindings,
  DashboardTopics,
  DashboardWork,
  DocumentInfo,
  FeatureName,
  FeatureScores,
  FeedResponse,
  GraphEdge,
  GraphNode,
  Job,
  LibraryItem,
  Me,
  Neighborhood,
  PaperDetail,
  PaperSummary,
  Period,
  Project,
  ProjectSettings,
  RecommendationItem,
  Slate,
  TopicStat,
  User,
} from "../api/types";

const FEATURES: readonly FeatureName[] = ["question", "anchor", "keyword", "draft", "citation", "preference", "recency"];
const WEIGHTS: Readonly<Record<FeatureName, number>> = {
  question: 0.3,
  anchor: 0.25,
  keyword: 0.2,
  draft: 0.15,
  citation: 0.07,
  preference: 0.15,
  recency: 0.08,
};

const NOW = "2026-10-12T08:00:00Z";

function letter(n: number): string {
  return String.fromCharCode(65 + ((n - 1) % 26));
}

export function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: 1,
    kind: "build_deck",
    status: "queued",
    project_id: 1,
    params: {},
    stats: {},
    progress: null,
    message: null,
    error_code: null,
    error_message: null,
    created_at: NOW,
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

export function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: 1,
    email: "test-user@example.org",
    email_verified: true,
    display_name: "Test User",
    avatar_url: null,
    is_admin: true,
    has_password: true,
    identities: [],
    preferences: { auto_expand: true, timezone: null },
    created_at: NOW,
    ...overrides,
  };
}

export function makeMe(overrides: Partial<Me> = {}): Me {
  return { mode: "accounts", user: makeUser(), ...overrides };
}

export function makeAuthConfig(overrides: Partial<AuthConfig> = {}): AuthConfig {
  return {
    mode: "accounts",
    providers: { password: true, google: true, github: true },
    allowlist: false,
    email_delivery: true,
    password_signup: true,
    email_verification: false,
    ...overrides,
  };
}

export function makePaperSummary(overrides: Partial<PaperSummary> = {}): PaperSummary {
  const id = overrides.id ?? 1;
  return {
    id,
    title: `Test paper ${letter(id)}`,
    authors: [
      { name: "Test Author One", position: 0, openalex_id: null },
      { name: "Test Author Two", position: 1, openalex_id: null },
    ],
    abstract: `Synthetic abstract for test paper ${letter(id)}.`,
    publication_date: "2026-10-10",
    year: 2026,
    primary_category: "cs.IR",
    categories: ["cs.IR"],
    venue: null,
    identifiers: { doi: null, arxiv: `2610.0000${id}`, openalex: null, s2: null },
    latest_version: "v1",
    links: { abs: null, pdf: null, doi: null, oa: null },
    topics: [{ concept_id: 100 + id, label: `Test topic ${letter(id)}`, score: 0.9 }],
    status: "active",
    is_oa: null,
    cited_by_count: null,
    cd: null,
    read_at: null,
    ...overrides,
  };
}

export function makeFeatureScores(overrides: Partial<FeatureScores> = {}): FeatureScores {
  const scores = {} as FeatureScores;
  for (const name of FEATURES) {
    scores[name] = { value: 0.5, raw: 0.5, weight: WEIGHTS[name], contribution: 0.5 * WEIGHTS[name], available: true };
  }
  return { ...scores, ...overrides };
}

export function makeRecommendationItem(overrides: Partial<RecommendationItem> = {}): RecommendationItem {
  const paper = overrides.paper ?? makePaperSummary();
  return {
    slate_id: 1,
    paper,
    rank: 1,
    score: 0.42,
    features: makeFeatureScores(),
    reasons: [
      {
        code: "category",
        kind: "metadata",
        text: "Listed in cs.IR, one of your project's categories",
        evidence: { category: "cs.IR" },
        contribution: null,
      },
    ],
    state: null,
    last_interaction_id: null,
    ...overrides,
  };
}

/** A slate whose items are papers `paperIds` in rank order. */
export function makeSlate(paperIds: readonly number[] = [1, 2, 3], overrides: Partial<Slate> = {}): Slate {
  const id = overrides.id ?? 1;
  const items =
    overrides.items ??
    paperIds.map((paperId, index) =>
      makeRecommendationItem({ slate_id: id, rank: index + 1, paper: makePaperSummary({ id: paperId }) }),
    );
  return {
    id,
    project_id: 1,
    generated_at: NOW,
    mode: "daily",
    ranker_version: "test-ranker",
    embedding_model: "local/hashing-v1",
    pool_size: 100,
    items,
    remaining: items.filter((item) => item.state === null).length,
    message: null,
    ...overrides,
  };
}

export function makeFeed(overrides: Partial<FeedResponse> = {}): FeedResponse {
  return { status: "ready", slate: makeSlate(), job: null, last_job: null, message: null, ...overrides };
}

export const DEFAULT_PROJECT_SETTINGS: ProjectSettings = {
  arxiv_categories: ["cs.IR"],
  time_window_days: 14,
  slate_size: 20,
};

export function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: 1,
    name: "Test project",
    research_question: "Test research question?",
    description: "",
    keywords: [],
    settings: { ...DEFAULT_PROJECT_SETTINGS },
    active: true,
    created_at: NOW,
    updated_at: NOW,
    stats: {
      seeds: 0,
      documents: 0,
      relevant: 0,
      saved: 0,
      not_relevant: 0,
      remaining_in_deck: 0,
      last_deck_at: null,
    },
    ...overrides,
  };
}

export function makePaperDetail(overrides: Partial<PaperDetail> = {}): PaperDetail {
  const summary = makePaperSummary({ id: overrides.id ?? 1 });
  return {
    ...summary,
    provenance: {},
    counts: { references: null, citations: null },
    state: null,
    last_interaction_id: null,
    recommendation: null,
    expansion: { status: "none", job_id: null, last_expanded_at: null, message: null },
    disruption: null,
    ...overrides,
  };
}

export function makeLibraryItem(overrides: Partial<LibraryItem> = {}): LibraryItem {
  return {
    paper: makePaperSummary(),
    state: "saved",
    updated_at: NOW,
    last_interaction_id: 1,
    ...overrides,
  };
}

export function makeDocument(overrides: Partial<DocumentInfo> = {}): DocumentInfo {
  return {
    id: 1,
    project_id: 1,
    filename: "test-draft.md",
    mime_type: "text/markdown",
    size_bytes: 1024,
    status: "ready",
    error: null,
    n_sections: 1,
    n_chunks: 2,
    n_pages: null,
    created_at: NOW,
    outline: [],
    job_id: null,
    progress: null,
    message: null,
    ...overrides,
  };
}

export function makeGraphNode(overrides: Partial<GraphNode> = {}): GraphNode {
  const paper = overrides.paper === undefined ? makePaperSummary() : overrides.paper;
  return {
    id: `work:${paper?.id ?? 1}`,
    type: "work",
    label: paper?.title ?? "Test paper A",
    paper,
    scholar: null,
    state: null,
    seed: false,
    is_center: false,
    weight: 0.5,
    ...overrides,
  };
}

export function makeGraphEdge(overrides: Partial<GraphEdge> = {}): GraphEdge {
  return {
    id: "e:1",
    source: "work:1",
    target: "work:2",
    relation: "CITES",
    verified: true,
    confidence: null,
    provenance: "openalex",
    evidence: { relation: "CITES", contexts: [], intents: [], is_influential: null },
    ...overrides,
  };
}

export function makeNeighborhood(overrides: Partial<Neighborhood> = {}): Neighborhood {
  return {
    center: "work:1",
    nodes: [makeGraphNode({ is_center: true })],
    edges: [],
    groups: [],
    expansion: { status: "none", job_id: null, last_expanded_at: null, message: null },
    notices: [],
    ...overrides,
  };
}

export function makePeriod(overrides: Partial<Period> = {}): Period {
  return { start: "2026-09-13", end: "2026-10-12", days: 30, bucket: "day", tz: "UTC", ...overrides };
}

export function makeActivity(overrides: Partial<DashboardActivity> = {}): DashboardActivity {
  const totals = { swiped: 12, relevant: 5, not_relevant: 5, saved: 2, opened: 4, read: 3, decks: 6 };
  return {
    period: makePeriod(),
    totals,
    previous: { swiped: 8, relevant: 2, not_relevant: 5, saved: 1, opened: 2, read: 1, decks: 4 },
    all_time: { swiped: 40, read: 9, library: 14 },
    series: [
      { date: "2026-10-11", swiped: 5, positive: 3 },
      { date: "2026-10-12", swiped: 7, positive: 4 },
    ],
    positive_rate: 0.5833,
    positive_rate_before: 0.375,
    streak_days: 2,
    by_project: [{ project_id: 1, name: "Test project", swiped: 12, positive: 7 }],
    ...overrides,
  };
}

export function makeTopicStat(overrides: Partial<TopicStat> = {}): TopicStat {
  return {
    kind: "category",
    key: "cat:cs.LG",
    label: "Machine Learning (cs.LG)",
    concept_id: null,
    category: "cs.LG",
    count: 3,
    share: 0.75,
    count_before: 0,
    share_before: 0,
    delta: 0.75,
    weekly: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 2],
    swiped: 4,
    skip_rate: 0.25,
    ...overrides,
  };
}

export function makeTopics(overrides: Partial<DashboardTopics> = {}): DashboardTopics {
  const stat = makeTopicStat();
  return {
    period: makePeriod(),
    weeks: Array.from({ length: 12 }, (_, i) => `2026-07-${String(20 + i).padStart(2, "0")}`),
    n_positive: 4,
    n_positive_before: 3,
    leaning_in: [stat],
    fading: [],
    top: [stat],
    skipping: [],
    coverage: 0,
    ...overrides,
  };
}

export function makeFindings(overrides: Partial<DashboardFindings> = {}): DashboardFindings {
  return {
    period: makePeriod(),
    n_papers: 2,
    n_with_findings: 1,
    groups: [
      {
        topic: { kind: "category", key: "cat:cs.LG", label: "Machine Learning (cs.LG)", concept_id: null, category: "cs.LG" },
        findings: [
          {
            paper: makePaperSummary({ id: 1 }),
            sentence: "Experiments show that it outperforms dense baselines by 12% on three benchmarks.",
            cues: ["results indicate", "outperforms"],
            score: 6,
            similarity: 0.62,
            action: "relevant",
            acted_at: NOW,
          },
        ],
      },
    ],
    summary: null,
    ai: { available: false, model: null, remaining_today: 0, job: null },
    ...overrides,
  };
}

export function makeWork(overrides: Partial<DashboardWork> = {}): DashboardWork {
  return {
    period: makePeriod(),
    projects: [
      {
        project_id: 1,
        name: "Test project",
        has_documents: false,
        threshold: 0.3,
        closest: [],
        quartiles: [],
        correlation: null,
        sections: [],
        drift: Array.from({ length: 12 }, (_, i) => ({
          week: `2026-07-${String(20 + i).padStart(2, "0")}`,
          mean_similarity: null,
          n: 0,
        })),
      },
    ],
    ...overrides,
  };
}
