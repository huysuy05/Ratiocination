/**
 * Project keywords (API.md "Projects", SPEC §10): the same normalisation the server uses to match and
 * de-duplicate them (backend/radar/ranking/keywords.py), so the chip input can say "Already added" for
 * "LLMs" after "LLM" or "self supervised" after "Self-Supervised".
 */

export const MAX_KEYWORDS = 12;
export const MIN_KEYWORD_LENGTH = 2;
export const MAX_KEYWORD_LENGTH = 60;

const SEPARATORS = /[-\u2010-\u2015\u2212\u2043\ufe58\ufe63\uff0d/\\_]/gu;
const TOKEN = /[\p{L}\p{N}\p{M}]+[+#]*/gu;

function stem(token: string): string {
  if (token.length < 4 || !/^\p{L}+$/u.test(token)) return token;
  if (token.endsWith("ies") && token.length > 4) return `${token.slice(0, -3)}y`;
  if (["sses", "xes", "zes", "ches", "shes"].some((end) => token.endsWith(end))) return token.slice(0, -2);
  if (token.endsWith("s") && !["ss", "us", "is"].some((end) => token.endsWith(end))) return token.slice(0, -1);
  return token;
}

/** The match key: NFKC, lower case, hyphens as spaces, light plural handling ("" matches nothing). */
export function keywordKey(keyword: string): string {
  const text = keyword.normalize("NFKC").replace(SEPARATORS, " ");
  return Array.from(text.matchAll(TOKEN), (match) => stem(match[0].toLowerCase())).join(" ");
}

/** A keyword as stored: trimmed, inner whitespace collapsed. */
export function cleanKeyword(value: string): string {
  return value.normalize("NFC").replace(/\s+/gu, " ").trim();
}

/** Why `candidate` cannot be added to `existing`, or null when it can. */
export function keywordProblem(candidate: string, existing: readonly string[]): string | null {
  const keyword = cleanKeyword(candidate);
  if (keyword.length < MIN_KEYWORD_LENGTH || keyword.length > MAX_KEYWORD_LENGTH) {
    return `Keywords are ${MIN_KEYWORD_LENGTH}–${MAX_KEYWORD_LENGTH} characters long.`;
  }
  const key = keywordKey(keyword);
  if (!key) return "A keyword needs at least one letter or digit.";
  const twin = existing.find((other) => keywordKey(other) === key);
  if (twin !== undefined) return `“${twin}” is already in the list.`;
  if (existing.length >= MAX_KEYWORDS) return `Up to ${MAX_KEYWORDS} keywords.`;
  return null;
}

/** Splits pasted text into keywords: commas, semicolons and new lines separate them. */
export function splitKeywords(text: string): string[] {
  return text
    .split(/[,;\n\r]+/u)
    .map(cleanKeyword)
    .filter((keyword) => keyword.length > 0);
}
