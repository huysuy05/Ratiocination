/**
 * arXiv categories offered by the project wizard (UI.md §10), grouped, with readable names. Codes
 * outside this list can still be typed (validated like the API: `^[a-z-]+(\.[A-Za-z-]+)?$`).
 */
export interface CategoryInfo {
  code: string;
  name: string;
}

export interface CategoryGroup {
  label: string;
  categories: CategoryInfo[];
}

export const CATEGORY_GROUPS: readonly CategoryGroup[] = [
  {
    label: "Computer science",
    categories: [
      { code: "cs.AI", name: "Artificial Intelligence" },
      { code: "cs.CL", name: "Computation and Language" },
      { code: "cs.CV", name: "Computer Vision" },
      { code: "cs.LG", name: "Machine Learning" },
      { code: "cs.IR", name: "Information Retrieval" },
      { code: "cs.HC", name: "Human-Computer Interaction" },
      { code: "cs.RO", name: "Robotics" },
      { code: "cs.NE", name: "Neural and Evolutionary Computing" },
      { code: "cs.CR", name: "Cryptography and Security" },
      { code: "cs.DB", name: "Databases" },
      { code: "cs.DC", name: "Distributed Computing" },
      { code: "cs.DL", name: "Digital Libraries" },
      { code: "cs.DS", name: "Data Structures and Algorithms" },
      { code: "cs.SE", name: "Software Engineering" },
      { code: "cs.PL", name: "Programming Languages" },
      { code: "cs.SI", name: "Social and Information Networks" },
      { code: "cs.CY", name: "Computers and Society" },
      { code: "cs.GT", name: "Game Theory" },
      { code: "cs.MA", name: "Multiagent Systems" },
      { code: "cs.GR", name: "Graphics" },
      { code: "cs.SD", name: "Sound" },
      { code: "cs.NI", name: "Networking" },
      { code: "cs.LO", name: "Logic in Computer Science" },
      { code: "cs.CC", name: "Computational Complexity" },
      { code: "cs.AR", name: "Hardware Architecture" },
      { code: "cs.OS", name: "Operating Systems" },
      { code: "cs.SY", name: "Systems and Control" },
      { code: "cs.CE", name: "Computational Engineering" },
    ],
  },
  {
    label: "Statistics",
    categories: [
      { code: "stat.ML", name: "Machine Learning (Statistics)" },
      { code: "stat.ME", name: "Statistical Methodology" },
      { code: "stat.AP", name: "Statistics Applications" },
      { code: "stat.CO", name: "Statistical Computation" },
      { code: "stat.TH", name: "Statistics Theory" },
    ],
  },
  {
    label: "Mathematics",
    categories: [
      { code: "math.OC", name: "Optimization and Control" },
      { code: "math.ST", name: "Statistics Theory" },
      { code: "math.PR", name: "Probability" },
      { code: "math.NA", name: "Numerical Analysis" },
    ],
  },
  {
    label: "Electrical engineering and systems",
    categories: [
      { code: "eess.AS", name: "Audio and Speech Processing" },
      { code: "eess.IV", name: "Image and Video Processing" },
      { code: "eess.SP", name: "Signal Processing" },
      { code: "eess.SY", name: "Systems and Control" },
    ],
  },
  {
    label: "Quantitative biology and finance",
    categories: [
      { code: "q-bio.BM", name: "Biomolecules" },
      { code: "q-bio.GN", name: "Genomics" },
      { code: "q-bio.NC", name: "Neurons and Cognition" },
      { code: "q-bio.QM", name: "Quantitative Methods" },
      { code: "q-fin.CP", name: "Computational Finance" },
      { code: "q-fin.ST", name: "Statistical Finance" },
    ],
  },
  {
    label: "Economics and physics",
    categories: [
      { code: "econ.EM", name: "Econometrics" },
      { code: "econ.GN", name: "General Economics" },
      { code: "physics.comp-ph", name: "Computational Physics" },
      { code: "physics.soc-ph", name: "Physics and Society" },
      { code: "physics.data-an", name: "Data Analysis and Statistics" },
      { code: "quant-ph", name: "Quantum Physics" },
      { code: "cond-mat.stat-mech", name: "Statistical Mechanics" },
      { code: "cond-mat.dis-nn", name: "Disordered Systems and Neural Networks" },
      { code: "astro-ph.IM", name: "Astrophysics Instrumentation and Methods" },
      { code: "hep-th", name: "High Energy Physics - Theory" },
      { code: "hep-ph", name: "High Energy Physics - Phenomenology" },
      { code: "gr-qc", name: "General Relativity and Quantum Cosmology" },
    ],
  },
];

export const CATEGORY_NAMES: ReadonlyMap<string, string> = new Map(
  CATEGORY_GROUPS.flatMap((group) => group.categories.map((category) => [category.code, category.name] as const)),
);

/** API validation of a category code. */
export const CATEGORY_PATTERN = /^[a-z-]+(\.[A-Za-z-]+)?$/;
export const MAX_CATEGORIES = 20;

/** "Machine Learning (cs.LG)", or the bare code when unknown. */
export function categoryLabel(code: string): string {
  const name = CATEGORY_NAMES.get(code);
  return name ? `${name} (${code})` : code;
}

/** Case-insensitive match on code or name. */
export function matchesCategory(category: CategoryInfo, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return category.code.toLowerCase().includes(q) || category.name.toLowerCase().includes(q);
}
