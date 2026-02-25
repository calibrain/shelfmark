import { Book, Release, ReleasesResponse } from '../types';

function normalizeMatchText(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function collectNormalizedStrings(values: Array<string | null | undefined>): string[] {
  const seen = new Set<string>();
  const normalizedValues: string[] = [];

  for (const value of values) {
    if (!value) continue;
    const normalized = normalizeMatchText(value);
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    normalizedValues.push(normalized);
  }

  return normalizedValues;
}

function getLocalizedTitleValues(raw: unknown): string[] {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return [];
  }

  const values: string[] = [];
  for (const value of Object.values(raw as Record<string, unknown>)) {
    if (typeof value === 'string' && value.trim()) {
      values.push(value);
    }
  }
  return values;
}

function splitAuthorString(author: string): string[] {
  return author
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean);
}

export function getBookTitleCandidates(
  uiBook: Book | null,
  responseBook: ReleasesResponse['book'] | undefined
): string[] {
  return collectNormalizedStrings([
    responseBook?.search_title,
    responseBook?.title,
    ...getLocalizedTitleValues(responseBook?.titles_by_language),
    uiBook?.search_title,
    uiBook?.title,
    ...getLocalizedTitleValues(uiBook?.titles_by_language),
  ]);
}

export function getBookAuthorCandidates(
  uiBook: Book | null,
  responseBook: ReleasesResponse['book'] | undefined
): string[] {
  const responseAuthors = responseBook?.authors ?? [];
  const uiAuthors = uiBook?.authors ?? [];
  const uiAuthorParts = uiBook?.author ? splitAuthorString(uiBook.author) : [];
  return collectNormalizedStrings([
    responseBook?.search_author,
    ...responseAuthors,
    uiBook?.search_author,
    ...uiAuthors,
    ...uiAuthorParts,
  ]);
}

function getReleaseAuthorForMatch(release: Release): string | null {
  const rawAuthor = release.extra?.author;
  if (typeof rawAuthor !== 'string') {
    return null;
  }

  const normalized = normalizeMatchText(rawAuthor);
  return normalized || null;
}

function hasAuthorMatch(release: Release, authorCandidates: string[]): boolean {
  if (authorCandidates.length === 0) {
    return false;
  }

  const releaseAuthor = getReleaseAuthorForMatch(release);
  if (!releaseAuthor) {
    return false;
  }

  const releaseTokens = new Set(releaseAuthor.split(' ').filter(Boolean));
  return authorCandidates.some((candidate) => {
    const candidateTokens = candidate.split(' ').filter(Boolean);
    return candidateTokens.length > 0 && candidateTokens.every((token) => releaseTokens.has(token));
  });
}

const STOP_WORDS = new Set(['a', 'an', 'the', 'and', 'or', 'of', 'in', 'to', 'for', 'on', 'at', 'by', 'is']);

function removeStopWords(text: string): string {
  return text.split(' ').filter((w) => !STOP_WORDS.has(w)).join(' ');
}

function getTitleMatchScore(title: string, titleCandidate: string): number {
  const normalizedTitle = normalizeMatchText(title);
  if (!normalizedTitle || !titleCandidate) {
    return 0;
  }

  // Exact match on full normalized strings (highest score)
  if (normalizedTitle === titleCandidate) {
    return 10000;
  }

  // Also check with stop words stripped for substring comparisons
  const strippedTitle = removeStopWords(normalizedTitle);
  const strippedCandidate = removeStopWords(titleCandidate);

  let score = 0;

  if (normalizedTitle.startsWith(titleCandidate) || strippedTitle.startsWith(strippedCandidate)) {
    score += 6000;
  } else if (normalizedTitle.includes(titleCandidate) || strippedTitle.includes(strippedCandidate)) {
    score += 3000;
  }

  // Token overlap uses stop-word-stripped versions so only meaningful words are compared
  const candidateTokens = strippedCandidate.split(' ').filter((token) => token.length >= 3);
  if (candidateTokens.length > 0) {
    const titleTokens = new Set(strippedTitle.split(' '));
    const matchedTokens = candidateTokens.filter((token) => titleTokens.has(token)).length;
    score += Math.round((matchedTokens / candidateTokens.length) * 2500);
  }

  // Prefer closer-length titles when match quality is otherwise similar.
  score -= Math.min(Math.abs(normalizedTitle.length - titleCandidate.length), 100);

  return score;
}

export function sortReleasesByBookMatch(
  releases: Release[],
  titleCandidates: string[],
  authorCandidates: string[]
): Release[] {
  if (titleCandidates.length === 0) {
    return releases;
  }

  return releases
    .map((release, index) => ({
      release,
      index,
      score: titleCandidates.reduce((best, candidate) => (
        Math.max(best, getTitleMatchScore(release.title, candidate))
      ), 0) + (hasAuthorMatch(release, authorCandidates) ? 1500 : 0),
    }))
    .sort((a, b) => {
      const scoreDiff = b.score - a.score;
      if (scoreDiff !== 0) {
        return scoreDiff;
      }
      return a.index - b.index;
    })
    .map(({ release }) => release);
}
