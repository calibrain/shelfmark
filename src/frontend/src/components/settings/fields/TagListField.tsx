import { useMemo, useRef, useState } from 'react';
import { TagListFieldConfig } from '../../../types/settings';

interface TagListFieldProps {
  field: TagListFieldConfig;
  value: string[];
  onChange: (value: string[]) => void;
  disabled?: boolean;
  requiredTags?: string[];  // Tags that cannot be removed
}

function normalizeTag(raw: string, normalizeUrls: boolean): string {
  let s = raw.trim();
  if (!s) return '';

  // Strip wrapping quotes from pasted env/JSON style strings.
  if (
    (s.startsWith('"') && s.endsWith('"')) ||
    (s.startsWith("'") && s.endsWith("'"))
  ) {
    s = s.slice(1, -1).trim();
  }

  if (normalizeUrls) {
    // Avoid special sentinel values.
    if (s.toLowerCase() === 'auto') return '';

    // Basic URL normalization to keep UX friendly; backend also normalizes on save.
    // Only add https:// if it looks like a domain (contains a dot) and has no protocol.
    // This avoids adding prefixes to non-URL values like OIDC scopes (openid, email, etc.)
    if (!s.includes('://') && !s.startsWith('/') && s.includes('.')) {
      s = `https://${s}`;
    }
    s = s.replace(/\/+$/, '');
  }
  return s.trim();
}

export const TagListField = ({ field, value, onChange, disabled, requiredTags }: TagListFieldProps) => {
  const isDisabled = disabled ?? false;
  const required = requiredTags ?? [];
  const inputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState('');
  const normalizeUrls = field.normalizeUrls ?? true;

  const tags = useMemo(() => (value ?? []).map(String).filter((t) => t.trim() !== ''), [value]);

  const addFromRaw = (raw: string) => {
    if (isDisabled) return;
    const parts = raw
      .split(/[\n,]+/)
      .map((p) => p.trim())
      .filter(Boolean);
    if (parts.length === 0) return;

    const next = [...tags];
    for (const part of parts) {
      const normalized = normalizeTag(part, normalizeUrls);
      if (!normalized) continue;
      if (next.includes(normalized)) continue;
      next.push(normalized);
    }

    if (next.length !== tags.length) {
      onChange(next);
    }
  };

  const isRequired = (tag: string) => required.includes(tag);

  const removeAt = (idx: number) => {
    if (isDisabled || isRequired(tags[idx])) return;
    onChange(tags.filter((_, i) => i !== idx));
  };

  const commitDraft = () => {
    const raw = draft;
    if (!raw.trim()) return;
    addFromRaw(raw);
    setDraft('');
  };

  return (
    <div
      className={`w-full px-3 py-2 rounded-lg border border-[var(--border-muted)]
                  bg-[var(--bg-soft)] text-sm
                  focus-within:outline-none focus-within:ring-2 focus-within:ring-sky-500/50 focus-within:border-sky-500
                  transition-colors
                  ${isDisabled ? 'opacity-60 cursor-not-allowed' : 'cursor-text'}`}
      onClick={() => {
        if (isDisabled) return;
        inputRef.current?.focus();
      }}
    >
      <div className="flex flex-wrap gap-1 items-center min-h-[1.25rem]">
        {tags.map((tag, idx) => (
          <span
            key={`${tag}-${idx}`}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md
                       border border-[var(--border-muted)] bg-[var(--bg)]
                       max-w-full"
            title={tag}
          >
            <span className="truncate max-w-[22rem]">{tag}</span>
            {!isDisabled && !isRequired(tag) && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  removeAt(idx);
                }}
                className="p-0.5 rounded hover:bg-[var(--hover-surface)]"
                aria-label={`Remove ${tag}`}
              >
                <svg
                  className="w-4 h-4"
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth={1.5}
                  stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </span>
        ))}

        {!isDisabled && (
          <input
            ref={inputRef}
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                commitDraft();
                return;
              }

              if (e.key === 'Backspace' && !draft && tags.length > 0) {
                removeAt(tags.length - 1);
              }
            }}
            onBlur={() => commitDraft()}
            placeholder={tags.length === 0 ? field.placeholder : ''}
            className="flex-1 min-w-[4rem] bg-transparent outline-none px-1 py-0"
          />
        )}

        {isDisabled && tags.length === 0 && (
          <span className="opacity-60">{field.placeholder || 'No values'}</span>
        )}
      </div>
    </div>
  );
};
