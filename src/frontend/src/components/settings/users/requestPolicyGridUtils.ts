import { RequestPolicyMode } from '../../../types';
import { TableFieldConfig } from '../../../types/settings';

export type RequestPolicyContentType = 'ebook' | 'audiobook';
export type RequestPolicyMatrixMode = Exclude<RequestPolicyMode, 'request_book'>;

export interface RequestPolicyRuleRow {
  source: string;
  content_type: RequestPolicyContentType;
  mode: RequestPolicyMatrixMode;
}

export interface RequestPolicyDefaultsValue {
  ebook: RequestPolicyMode;
  audiobook: RequestPolicyMode;
}

export interface RequestPolicySourceCapability {
  source: string;
  displayName: string;
  supportedContentTypes: RequestPolicyContentType[];
}

export const REQUEST_POLICY_DEFAULT_OPTIONS: Array<{
  value: RequestPolicyMode;
  label: string;
  description: string;
}> = [
  {
    value: 'download',
    label: 'Download',
    description: 'Everything can be downloaded directly.',
  },
  {
    value: 'request_release',
    label: 'Request Release',
    description: 'Users must request a specific release.',
  },
  {
    value: 'request_book',
    label: 'Request Book',
    description: 'Users request a book, admin picks the release.',
  },
  {
    value: 'blocked',
    label: 'Blocked',
    description: 'No downloads or requests allowed.',
  },
];

export const REQUEST_POLICY_MODE_LABELS: Record<RequestPolicyMode, string> = {
  download: 'Download',
  request_release: 'Request Release',
  request_book: 'Request Book',
  blocked: 'Blocked',
};

const MATRIX_MODES: RequestPolicyMatrixMode[] = ['download', 'request_release', 'blocked'];
const CONTENT_TYPES: RequestPolicyContentType[] = ['ebook', 'audiobook'];
const MODE_RANK: Record<RequestPolicyMode, number> = {
  download: 0,
  request_release: 1,
  request_book: 2,
  blocked: 3,
};

const normalizeSource = (value: unknown): string => String(value || '').trim().toLowerCase();

const normalizeContentType = (value: unknown): RequestPolicyContentType | null => {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'ebook') return 'ebook';
  if (normalized === 'audiobook') return 'audiobook';
  return null;
};

const normalizeMode = (
  value: unknown,
  options: readonly RequestPolicyMode[] = ['download', 'request_release', 'request_book', 'blocked']
): RequestPolicyMode | null => {
  const normalized = String(value || '').trim().toLowerCase() as RequestPolicyMode;
  return options.includes(normalized) ? normalized : null;
};

const toRuleKey = (source: string, contentType: RequestPolicyContentType) => `${source}::${contentType}`;

export const sortRules = (rules: RequestPolicyRuleRow[]): RequestPolicyRuleRow[] =>
  [...rules].sort((a, b) => {
    const sourceCmp = a.source.localeCompare(b.source);
    if (sourceCmp !== 0) return sourceCmp;
    return a.content_type.localeCompare(b.content_type);
  });

export const normalizeRequestPolicyDefaults = (
  raw: Partial<Record<RequestPolicyContentType, unknown>>,
  fallback: RequestPolicyMode = 'download'
): RequestPolicyDefaultsValue => {
  const fallbackMode = normalizeMode(fallback) || 'download';
  return {
    ebook: normalizeMode(raw.ebook) || fallbackMode,
    audiobook: normalizeMode(raw.audiobook) || fallbackMode,
  };
};

export const normalizeRequestPolicyRules = (rawRules: unknown): RequestPolicyRuleRow[] => {
  if (!Array.isArray(rawRules)) {
    return [];
  }

  const byKey = new Map<string, RequestPolicyRuleRow>();
  rawRules.forEach((rawRule) => {
    if (!rawRule || typeof rawRule !== 'object') {
      return;
    }
    const row = rawRule as Record<string, unknown>;
    const source = normalizeSource(row.source);
    const contentType = normalizeContentType(row.content_type);
    const mode = normalizeMode(row.mode, MATRIX_MODES) as RequestPolicyMatrixMode | null;
    if (!source || !contentType || !mode) {
      return;
    }
    byKey.set(toRuleKey(source, contentType), {
      source,
      content_type: contentType,
      mode,
    });
  });

  return sortRules([...byKey.values()]);
};

export const capPolicyMode = (mode: RequestPolicyMode, ceiling: RequestPolicyMode): RequestPolicyMode => {
  return MODE_RANK[mode] < MODE_RANK[ceiling] ? ceiling : mode;
};

export const isMatrixConfigurable = (defaultMode: RequestPolicyMode): boolean => {
  return defaultMode !== 'blocked';
};

export const getAllowedMatrixModes = (defaultMode: RequestPolicyMode): RequestPolicyMatrixMode[] => {
  if (!isMatrixConfigurable(defaultMode)) {
    return [];
  }

  return MATRIX_MODES.filter((mode) => MODE_RANK[mode] >= MODE_RANK[defaultMode]);
};

export const mergeRequestPolicyRuleLayers = (
  baseRules: RequestPolicyRuleRow[],
  overrideRules: RequestPolicyRuleRow[]
): RequestPolicyRuleRow[] => {
  const merged = new Map<string, RequestPolicyRuleRow>();
  baseRules.forEach((rule) => {
    merged.set(toRuleKey(rule.source, rule.content_type), rule);
  });
  overrideRules.forEach((rule) => {
    merged.set(toRuleKey(rule.source, rule.content_type), rule);
  });
  return sortRules([...merged.values()]);
};

const findRule = (
  rules: RequestPolicyRuleRow[],
  source: string,
  contentType: RequestPolicyContentType
): RequestPolicyRuleRow | null => {
  const normalizedSource = normalizeSource(source);
  return (
    rules.find(
      (rule) => rule.source === normalizedSource && rule.content_type === contentType
    ) || null
  );
};

export const getInheritedCellMode = (
  source: string,
  contentType: RequestPolicyContentType,
  defaultModes: RequestPolicyDefaultsValue,
  baseRules: RequestPolicyRuleRow[]
): RequestPolicyMode => {
  const ceiling = defaultModes[contentType];
  const fromRule = findRule(baseRules, source, contentType)?.mode;
  return capPolicyMode(fromRule || ceiling, ceiling);
};

export const getEffectiveCellMode = (
  source: string,
  contentType: RequestPolicyContentType,
  defaultModes: RequestPolicyDefaultsValue,
  baseRules: RequestPolicyRuleRow[],
  explicitRules: RequestPolicyRuleRow[]
): RequestPolicyMode => {
  const ceiling = defaultModes[contentType];
  const explicit = findRule(explicitRules, source, contentType)?.mode;
  if (explicit) {
    return capPolicyMode(explicit, ceiling);
  }
  return getInheritedCellMode(source, contentType, defaultModes, baseRules);
};

export const parseSourceCapabilitiesFromRulesField = (
  rulesField: TableFieldConfig | null | undefined,
  fallbackSources: string[] = []
): RequestPolicySourceCapability[] => {
  if (!rulesField || !Array.isArray(rulesField.columns)) {
    return fallbackSources.map((source) => ({
      source: normalizeSource(source),
      displayName: source,
      supportedContentTypes: ['ebook', 'audiobook'],
    }));
  }

  const sourceColumn = rulesField.columns.find((column) => column.key === 'source');
  const contentTypeColumn = rulesField.columns.find((column) => column.key === 'content_type');

  const sourceOptions = sourceColumn?.options ?? [];
  const contentTypeOptions = contentTypeColumn?.options ?? [];
  const bySource = new Map<string, RequestPolicySourceCapability>();

  sourceOptions.forEach((option) => {
    const source = normalizeSource(option.value);
    if (!source) {
      return;
    }
    bySource.set(source, {
      source,
      displayName: option.label || source,
      supportedContentTypes: [],
    });
  });

  contentTypeOptions.forEach((option) => {
    const source = normalizeSource(option.childOf);
    const contentType = normalizeContentType(option.value);
    if (!source || !contentType) {
      return;
    }

    const existing = bySource.get(source) || {
      source,
      displayName: source,
      supportedContentTypes: [],
    };

    if (!existing.supportedContentTypes.includes(contentType)) {
      existing.supportedContentTypes.push(contentType);
    }
    bySource.set(source, existing);
  });

  fallbackSources.forEach((sourceValue) => {
    const source = normalizeSource(sourceValue);
    if (!source || bySource.has(source)) {
      return;
    }
    bySource.set(source, {
      source,
      displayName: source,
      supportedContentTypes: ['ebook', 'audiobook'],
    });
  });

  const orderedSources = sourceOptions
    .map((option) => normalizeSource(option.value))
    .filter((source) => source && bySource.has(source));
  const extraSources = [...bySource.keys()].filter((source) => !orderedSources.includes(source));

  return [...orderedSources, ...extraSources].map((source) => {
    const row = bySource.get(source)!;
    const supported = CONTENT_TYPES.filter((contentType) =>
      row.supportedContentTypes.includes(contentType)
    );
    return {
      ...row,
      supportedContentTypes: supported,
    };
  });
};

const isSourceContentTypeSupported = (
  sourceCapabilities: RequestPolicySourceCapability[],
  source: string,
  contentType: RequestPolicyContentType
): boolean => {
  const normalizedSource = normalizeSource(source);
  const sourceCapability = sourceCapabilities.find((row) => row.source === normalizedSource);
  return Boolean(sourceCapability?.supportedContentTypes.includes(contentType));
};

export const normalizeExplicitRulesForPersistence = ({
  explicitRules,
  defaultModes,
  sourceCapabilities,
}: {
  explicitRules: RequestPolicyRuleRow[];
  /** @deprecated No longer used — kept for call-site compatibility */
  baseRules?: RequestPolicyRuleRow[];
  defaultModes: RequestPolicyDefaultsValue;
  sourceCapabilities: RequestPolicySourceCapability[];
}): RequestPolicyRuleRow[] => {
  const deduped = normalizeRequestPolicyRules(explicitRules);

  const filtered = deduped.filter((rule) => {
    if (!isSourceContentTypeSupported(sourceCapabilities, rule.source, rule.content_type)) {
      return false;
    }

    const defaultMode = defaultModes[rule.content_type];
    const allowedModes = getAllowedMatrixModes(defaultMode);
    if (!allowedModes.includes(rule.mode)) {
      return false;
    }

    return true;
  });

  return sortRules(filtered);
};

export const areRuleSetsEqual = (left: RequestPolicyRuleRow[], right: RequestPolicyRuleRow[]): boolean => {
  const leftSorted = sortRules(left);
  const rightSorted = sortRules(right);
  return JSON.stringify(leftSorted) === JSON.stringify(rightSorted);
};
