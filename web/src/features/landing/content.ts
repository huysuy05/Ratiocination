/**
 * Landing page copy and the made-up papers on its decorative mocks (UI.md §16). The demo papers
 * are illustrations, not real records; every mock that shows them is aria-hidden.
 */

export const LANDING_ASSETS = {
  logo: "/landing/ratiocination-logo.png",
  logoWhite: "/landing/ratiocination-logo-white.png",
  markWhite: "/landing/ratiocination-mark-white.png",
  /** 1207 × 197 (cropped to the artwork). */
  logoSize: { width: 1207, height: 197 },
  /** 205 × 197. */
  markSize: { width: 205, height: 197 },
  videoWebm: "/landing/ratiocination-loop.webm",
  videoMp4: "/landing/ratiocination-loop-web.mp4",
  poster: "/landing/ratiocination-loop-poster.jpg",
} as const;

export const SECTION_IDS = {
  howItWorks: "how-it-works",
  deck: "feature-deck",
  why: "feature-why",
  graph: "feature-graph",
} as const;

export { SOURCE_URL as REPO_URL } from "../../lib/source";

export interface DemoPaper {
  category: string;
  age: string;
  citedBy: number;
  title: string;
  authors: string;
  abstract: string;
  reasons: readonly { kind: "metadata" | "model"; text: string }[];
}

/** The hero deck, front to back: A, B, C. */
export const HERO_PAPERS: readonly [DemoPaper, DemoPaper, DemoPaper] = [
  {
    category: "cs.LG",
    age: "2 days ago",
    citedBy: 41,
    title: "Sparse routers learn faster when they forget: a study of expert collapse",
    authors: "A. Rahman, L. Chen, M. Okafor et al. · arXiv 2026",
    abstract: "Routers that periodically reset their gating statistics avoid expert collapse and reach the same loss 1.7× sooner.",
    reasons: [
      { kind: "metadata", text: "Cites your seed paper" },
      { kind: "model", text: "Matches §3 of your draft" },
    ],
  },
  {
    category: "cs.CL",
    age: "1 day ago",
    citedBy: 23,
    title: "Graph priors for retrieval-augmented reasoning over citation networks",
    authors: "M. Okafor, L. Chen, A. Diaz et al. · arXiv 2026",
    abstract:
      "A citation-graph prior steers retrieval toward papers that support each reasoning step, cutting unsupported claims by a third.",
    reasons: [
      { kind: "metadata", text: "Cites your seed paper" },
      { kind: "model", text: "Close to your question" },
    ],
  },
  {
    category: "cs.DL",
    age: "4 days ago",
    citedBy: 9,
    title: "Measuring disruption in machine learning with the CD index",
    authors: "J. Park, S. Varga et al. · arXiv 2026",
    abstract: "We compute the CD index for 410k ML papers and find that disruptive work clusters in small, new subfields.",
    reasons: [
      { kind: "metadata", text: "Shares 3 topics" },
      { kind: "model", text: "New in cs.DL" },
    ],
  },
];

export type TileArt = "rings" | "quote" | "graph" | "bars" | "disruption";

export interface CategoryTile {
  code: string;
  name: string;
  art: TileArt;
}

export const CATEGORY_TILES: readonly CategoryTile[] = [
  { code: "cs.LG", name: "Machine Learning", art: "rings" },
  { code: "cs.CL", name: "Computation & Language", art: "quote" },
  { code: "q-bio.NC", name: "Neurons & Cognition", art: "graph" },
  { code: "stat.ML", name: "Statistics · Machine Learning", art: "bars" },
  { code: "cs.DL", name: "Digital Libraries", art: "disruption" },
];

export interface Feature {
  id: string;
  title: readonly [string, string];
  body: string;
}

export const FEATURES = {
  deck: {
    id: SECTION_IDS.deck,
    title: ["Swipe a", "daily deck"],
    body: "Every morning Ratiocination ranks new arXiv papers against your research question, your seed papers and your draft. Swipe right for relevant, left for not relevant, up to save. The deck is finite: when it’s done, you’re done.",
  },
  why: {
    id: SECTION_IDS.why,
    title: ["Know why", "every paper"],
    body: "Each card says why it was picked: the seed paper it cites, the passage of your draft it matches, the topic you follow. Open the score breakdown to see how much each signal counted.",
  },
  graph: {
    id: SECTION_IDS.graph,
    title: ["Map the", "breakthroughs"],
    body: "Around the papers you keep grows a knowledge graph of citations, similar work, topics and authors. Each paper carries its disruption (CD) index from OpenAlex, so the breakthroughs stand out.",
  },
} as const satisfies Record<string, Feature>;
