import { useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { useDependencyEffect, useMountEffect } from '../hooks/useMountEffect';
import {
  addLibraryBook,
  fetchFieldOptions,
  getMetadataSearchConfig,
  searchMetadata,
  type DynamicFieldOption,
} from '../services/api';
import type { Book, ContentType, MetadataSearchConfig, MetadataSearchField } from '../types';
import { withBasePath } from '../utils/basePath';
import { buildQueryTargets } from '../utils/queryTargets';

type FieldValues = Record<string, string | number | boolean>;

const Cover = ({ book }: { book: Book }) => {
  const [failed, setFailed] = useState(false);
  if (!book.preview || failed) {
    return (
      <div className="flex aspect-[2/3] w-full items-center justify-center rounded-lg bg-(--hover-surface) text-xs opacity-60">
        No cover
      </div>
    );
  }
  return (
    <img
      src={withBasePath(book.preview)}
      alt={`Cover of ${book.title || 'untitled book'}`}
      className="aspect-[2/3] w-full rounded-lg object-cover"
      onError={() => setFailed(true)}
    />
  );
};

interface SearchFieldControlProps {
  field: MetadataSearchField;
  value: string | number | boolean;
  onChange: (value: string | number | boolean) => void;
}

const SearchFieldControl = ({ field, value, onChange }: SearchFieldControlProps) => {
  const [options, setOptions] = useState<DynamicFieldOption[]>([]);
  const [suggestions, setSuggestions] = useState<DynamicFieldOption[]>([]);
  const suggestionRequest = useRef(0);

  useDependencyEffect(() => {
    if (field.type !== 'DynamicSelectSearchField') return;
    void fetchFieldOptions(field.options_endpoint)
      .then(setOptions)
      .catch(() => setOptions([]));
  }, [field]);

  useDependencyEffect(() => {
    if (field.type !== 'TextSearchField' || !field.suggestions_endpoint) {
      setSuggestions([]);
      return;
    }
    const query = typeof value === 'string' ? value.trim() : '';
    const minimum = field.suggestions_min_query_length ?? 2;
    if (query.length < minimum) {
      setSuggestions([]);
      return;
    }
    const request = ++suggestionRequest.current;
    void fetchFieldOptions(field.suggestions_endpoint, query)
      .then((next) => {
        if (request === suggestionRequest.current) setSuggestions(next);
      })
      .catch(() => {
        if (request === suggestionRequest.current) setSuggestions([]);
      });
  }, [field, value]);

  if (field.type === 'CheckboxSearchField') {
    return (
      <label className="flex items-center gap-2 rounded-md border border-(--border-muted) px-3 py-2 text-sm">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(event.target.checked)}
        />
        {field.label}
      </label>
    );
  }

  if (field.type === 'SelectSearchField' || field.type === 'DynamicSelectSearchField') {
    const selectOptions = field.type === 'SelectSearchField' ? field.options : options;
    return (
      <label className="block text-sm font-medium">
        {field.label}
        <select
          value={String(value ?? '')}
          onChange={(event) => onChange(event.target.value)}
          className="mt-1 w-full rounded-md border border-(--border-muted) bg-(--bg) px-3 py-2 font-normal"
        >
          <option value="">Any</option>
          {selectOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
    );
  }

  const inputValue = typeof value === 'boolean' ? '' : value;
  return (
    <label className="relative block text-sm font-medium">
      {field.label}
      <input
        type={field.type === 'NumberSearchField' ? 'number' : 'search'}
        value={inputValue}
        min={field.type === 'NumberSearchField' ? field.min : undefined}
        max={field.type === 'NumberSearchField' ? field.max : undefined}
        step={field.type === 'NumberSearchField' ? field.step : undefined}
        placeholder={field.placeholder}
        onChange={(event) => {
          const next = event.target.value;
          onChange(field.type === 'NumberSearchField' && next ? Number(next) : next);
        }}
        className="mt-1 w-full rounded-md border border-(--border-muted) bg-transparent px-3 py-2 font-normal"
      />
      {suggestions.length > 0 && (
        <div className="absolute z-20 mt-1 w-full overflow-hidden rounded-md border border-(--border-muted) bg-(--bg) shadow-lg">
          {suggestions.map((option) => (
            <button
              key={option.value}
              type="button"
              className="hover-surface block w-full px-3 py-2 text-left text-sm font-normal"
              onClick={() => onChange(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
    </label>
  );
};

export const SearchPage = () => {
  const navigate = useNavigate();
  const [contentType, setContentType] = useState<ContentType>('ebook');
  const [config, setConfig] = useState<MetadataSearchConfig | null>(null);
  const [query, setQuery] = useState('');
  const [fields, setFields] = useState<FieldValues>({});
  const [activeTarget, setActiveTarget] = useState('general');
  const [contextOpen, setContextOpen] = useState(false);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [activeDynamicOptions, setActiveDynamicOptions] = useState<DynamicFieldOption[]>([]);
  const [activeSuggestions, setActiveSuggestions] = useState<DynamicFieldOption[]>([]);
  const configRequest = useRef(0);
  const activeSuggestionRequest = useRef(0);
  const contextHoverTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [sort, setSort] = useState('relevance');
  const [books, setBooks] = useState<Book[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [totalFound, setTotalFound] = useState(0);
  const [sourceUrl, setSourceUrl] = useState<string>();
  const [sourceTitle, setSourceTitle] = useState<string>();
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [addingId, setAddingId] = useState<string>();
  const [error, setError] = useState<string>();

  const clearContextHoverTimeout = () => {
    if (contextHoverTimeout.current) {
      clearTimeout(contextHoverTimeout.current);
      contextHoverTimeout.current = null;
    }
  };

  useMountEffect(() => clearContextHoverTimeout);

  useDependencyEffect(() => {
    const request = ++configRequest.current;
    setConfig(null);
    setFields({});
    void getMetadataSearchConfig(contentType)
      .then((next) => {
        if (request !== configRequest.current) return;
        setConfig(next);
        setActiveTarget('general');
        setFields(
          Object.fromEntries(
            next.search_fields.flatMap((field) =>
              field.type === 'CheckboxSearchField' && field.default
                ? [[field.key, true] as const]
                : [],
            ),
          ),
        );
        setSort(next.default_sort || 'relevance');
      })
      .catch((caught: unknown) => {
        if (request === configRequest.current) {
          setError(caught instanceof Error ? caught.message : 'Unable to load search');
        }
      });
  }, [contentType]);

  const runSearch = async (nextPage = 1, append = false) => {
    if (append) setLoadingMore(true);
    else setLoading(true);
    setError(undefined);
    try {
      const result = await searchMetadata(
        query,
        40,
        sort,
        fields,
        nextPage,
        contentType,
        config?.provider ?? undefined,
      );
      setBooks((current) => (append ? [...current, ...result.books] : result.books));
      setPage(result.page);
      setHasMore(result.hasMore);
      setTotalFound(result.totalFound);
      setSourceUrl(result.sourceUrl);
      setSourceTitle(result.sourceTitle);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to search for books');
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  };

  const addBookToLibrary = async (book: Book) => {
    if (!book.provider || !book.provider_id) return;
    setAddingId(book.id);
    setError(undefined);
    try {
      const result = await addLibraryBook(book.provider, book.provider_id);
      void navigate(`/library/${result.book_id}`, {
        state: { autoFindReleases: !result.files_exist_globally && !result.in_flight_globally },
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to add this book');
    } finally {
      setAddingId(undefined);
    }
  };

  const queryTargets = buildQueryTargets({ metadataSearchFields: config?.search_fields });
  const activeQueryTarget =
    queryTargets.find((target) => target.key === activeTarget) ?? queryTargets[0];
  const activeField = activeQueryTarget?.field;
  const activeValue = activeField ? (fields[activeField.key] ?? '') : query;
  const updateActiveValue = (value: string | number | boolean) => {
    if (activeField) {
      setFields((current) => ({ ...current, [activeField.key]: value }));
    } else {
      setQuery(String(value));
    }
  };

  useDependencyEffect(() => {
    if (activeField?.type !== 'DynamicSelectSearchField') {
      setActiveDynamicOptions([]);
      return;
    }
    void fetchFieldOptions(activeField.options_endpoint)
      .then(setActiveDynamicOptions)
      .catch(() => setActiveDynamicOptions([]));
  }, [activeField]);

  useDependencyEffect(() => {
    if (activeField?.type !== 'TextSearchField' || !activeField.suggestions_endpoint) {
      activeSuggestionRequest.current += 1;
      setActiveSuggestions([]);
      return;
    }
    const searchValue = typeof activeValue === 'string' ? activeValue.trim() : '';
    if (searchValue.length < (activeField.suggestions_min_query_length ?? 2)) {
      activeSuggestionRequest.current += 1;
      setActiveSuggestions([]);
      return;
    }
    const request = ++activeSuggestionRequest.current;
    void fetchFieldOptions(activeField.suggestions_endpoint, searchValue)
      .then((options) => {
        if (request === activeSuggestionRequest.current) setActiveSuggestions(options);
      })
      .catch(() => {
        if (request === activeSuggestionRequest.current) setActiveSuggestions([]);
      });
  }, [activeField, activeValue]);

  return (
    <section className="pb-16">
      <form
        id="search-section"
        className={`search-initial-state mb-6 ${books.length > 0 ? 'pt-0' : ''}`}
        onSubmit={(event) => {
          event.preventDefault();
          void runSearch();
        }}
      >
        <div
          className={`flex items-center justify-center gap-3 ${books.length > 0 ? 'hidden' : 'mb-6 opacity-100 sm:mb-8'}`}
        >
          <h1 className="text-2xl font-semibold">Search your library</h1>
        </div>
        <div className="search-wrapper flex flex-col gap-3">
          <div
            className="relative flex items-center rounded-full border"
            style={{ background: 'var(--bg-soft)', borderColor: 'var(--border-muted)' }}
          >
            <div
              className="relative flex shrink-0 self-stretch"
              onPointerEnter={(event) => {
                if (event.pointerType !== 'mouse') return;
                clearContextHoverTimeout();
                setContextOpen(true);
              }}
              onPointerLeave={(event) => {
                if (event.pointerType !== 'mouse') return;
                clearContextHoverTimeout();
                contextHoverTimeout.current = setTimeout(() => {
                  setContextOpen(false);
                  contextHoverTimeout.current = null;
                }, 150);
              }}
            >
              <button
                type="button"
                onClick={() => setContextOpen((open) => !open)}
                className="hover-action flex items-center gap-1.5 rounded-l-full px-5 pr-2"
                aria-label="Change search context"
                aria-expanded={contextOpen}
              >
                <svg
                  className="h-5 w-5"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth="1.5"
                  stroke="currentColor"
                  aria-hidden="true"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.967 8.967 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25"
                  />
                </svg>
                {activeTarget !== 'general' && (
                  <span className="hidden max-w-24 truncate text-sm font-medium sm:inline">
                    {activeQueryTarget?.label}
                  </span>
                )}
                <svg
                  className="h-3 w-3 opacity-50"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth="2.5"
                  stroke="currentColor"
                  aria-hidden="true"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="m19.5 8.25-7.5 7.5-7.5-7.5"
                  />
                </svg>
              </button>
              <div
                className="absolute top-1/2 right-0 h-6 w-px -translate-y-1/2"
                style={{ background: 'var(--border-muted)' }}
              />
              {contextOpen && (
                <div
                  className="animate-fade-in-down absolute top-full left-0 z-50 mt-2 w-[min(20rem,calc(100vw-2rem))] overflow-hidden rounded-2xl border shadow-2xl"
                  style={{ background: 'var(--bg)', borderColor: 'var(--border-muted)' }}
                  role="dialog"
                  aria-label="Search context"
                >
                  <div className="max-h-[min(24rem,calc(100vh-8rem))] overflow-y-auto p-3">
                    <div className="border-b pb-3" style={{ borderColor: 'var(--border-muted)' }}>
                      <div className="flex items-center justify-between px-1 pb-2">
                        <span className="text-xs font-medium tracking-wide uppercase opacity-60">
                          Content
                        </span>
                        <button
                          type="button"
                          onClick={() => setOptionsOpen((open) => !open)}
                          className={`rounded-xl px-3 py-2 text-xs font-medium ${optionsOpen ? 'bg-emerald-600 text-white' : 'hover-surface'}`}
                        >
                          Options
                        </button>
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        {(['ebook', 'audiobook'] as const).map((type) => (
                          <button
                            key={type}
                            type="button"
                            onClick={() => {
                              setContentType(type);
                              setContextOpen(false);
                            }}
                            className={`rounded-xl border px-3 py-2 text-sm font-medium ${contentType === type ? 'bg-emerald-600 text-white' : 'hover-surface'}`}
                            style={{
                              borderColor:
                                contentType === type
                                  ? 'rgb(16 185 129 / .7)'
                                  : 'var(--border-muted)',
                            }}
                          >
                            {type === 'ebook' ? 'Books' : 'Audiobooks'}
                          </button>
                        ))}
                      </div>
                    </div>
                    <div className="pt-2">
                      <div className="px-1 pb-1.5 text-xs font-medium tracking-wide uppercase opacity-60">
                        Search By
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        {queryTargets.map((target) => (
                          <button
                            key={target.key}
                            type="button"
                            onClick={() => {
                              setActiveTarget(target.key);
                              setContextOpen(false);
                            }}
                            className={`rounded-xl border px-3 py-2 text-left text-sm font-medium ${target.key === activeTarget ? 'bg-emerald-600 text-white' : 'hover-surface'}`}
                            style={{
                              borderColor:
                                target.key === activeTarget
                                  ? 'rgb(16 185 129 / .7)'
                                  : 'var(--border-muted)',
                            }}
                          >
                            {target.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
            {activeField?.type === 'CheckboxSearchField' && (
              <label className="flex min-w-0 flex-1 items-center gap-3 px-4 py-3 text-sm">
                <input
                  type="checkbox"
                  checked={Boolean(activeValue)}
                  onChange={(event) => updateActiveValue(event.target.checked)}
                />
                {activeField.label}
              </label>
            )}
            {(activeField?.type === 'SelectSearchField' ||
              activeField?.type === 'DynamicSelectSearchField') && (
              <select
                aria-label={`Search by ${activeQueryTarget?.label ?? 'General'}`}
                value={String(activeValue)}
                onChange={(event) => updateActiveValue(event.target.value)}
                className="min-w-0 flex-1 bg-transparent py-3 pl-3 outline-hidden"
              >
                <option value="">{activeField.placeholder || `Any ${activeField.label}`}</option>
                {(activeField.type === 'SelectSearchField'
                  ? activeField.options
                  : activeDynamicOptions
                ).map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            )}
            {activeField?.type !== 'CheckboxSearchField' &&
              activeField?.type !== 'SelectSearchField' &&
              activeField?.type !== 'DynamicSelectSearchField' && (
                <input
                  aria-label={`Search by ${activeQueryTarget?.label ?? 'General'}`}
                  type={activeField?.type === 'NumberSearchField' ? 'number' : 'search'}
                  value={typeof activeValue === 'boolean' ? '' : activeValue}
                  onChange={(event) =>
                    updateActiveValue(
                      activeField?.type === 'NumberSearchField' && event.target.value
                        ? Number(event.target.value)
                        : event.target.value,
                    )
                  }
                  placeholder={
                    activeField?.placeholder ||
                    (contentType === 'ebook' ? 'Search Books' : 'Search Audiobooks')
                  }
                  className="search-input min-w-0 flex-1 border-0 bg-transparent py-3 pl-3 outline-hidden"
                />
              )}
            {activeSuggestions.length > 0 && (
              <div
                className="absolute top-full right-0 left-0 z-50 mt-2 overflow-hidden rounded-2xl border shadow-xl"
                style={{ background: 'var(--bg)', borderColor: 'var(--border-muted)' }}
                role="listbox"
                aria-label={`${activeField?.label ?? 'Search'} suggestions`}
              >
                {activeSuggestions.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    role="option"
                    aria-selected={option.value === activeValue}
                    className="hover-surface block w-full px-5 py-3 text-left text-sm"
                    onClick={() => {
                      updateActiveValue(option.value);
                      setActiveSuggestions([]);
                    }}
                  >
                    <span className="block truncate font-medium">{option.label}</span>
                    {option.description && (
                      <span className="mt-0.5 block truncate text-xs opacity-70">
                        {option.description}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
            <button
              type="submit"
              disabled={loading || !config?.available}
              className="my-2 mr-2 flex items-center justify-center rounded-full bg-emerald-600 p-2 text-white hover:bg-emerald-700 disabled:opacity-60"
              aria-label="Search books"
            >
              <svg
                className="h-5 w-5"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth="2"
                stroke="currentColor"
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"
                />
              </svg>
            </button>
          </div>
          {optionsOpen && (
            <div className="grid gap-3 rounded-xl border border-(--border-muted) p-3 sm:grid-cols-2 lg:grid-cols-3">
              {config?.search_fields.map((field) => (
                <SearchFieldControl
                  key={field.key}
                  field={field}
                  value={
                    fields[field.key] ??
                    (field.type === 'CheckboxSearchField' ? Boolean(field.default) : '')
                  }
                  onChange={(value) => setFields((current) => ({ ...current, [field.key]: value }))}
                />
              ))}
            </div>
          )}
        </div>
      </form>
      {error && (
        <div className="mt-4 rounded-md border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}
      {books.length > 0 && (
        <div className="mt-8">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm opacity-65">
              {totalFound ? `${totalFound} results` : 'Results'}{' '}
              {sourceUrl && (
                <a
                  href={sourceUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="ml-2 text-violet-700 underline dark:text-violet-300"
                >
                  {sourceTitle || 'View source'}
                </a>
              )}
            </div>
            <label className="text-sm">
              Sort{' '}
              <select
                value={sort}
                onChange={(event) => setSort(event.target.value)}
                className="ml-1 rounded border border-(--border-muted) bg-(--bg) px-2 py-1"
              >
                {(config?.sort_options ?? []).map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="grid gap-6 sm:grid-cols-2 xl:grid-cols-3">
            {books.map((book) => (
              <article
                key={book.id}
                className="animate-pop-up flex min-h-36 overflow-hidden rounded-xl"
                style={{ background: 'var(--bg-soft)' }}
              >
                <div className="w-28 shrink-0">
                  <Cover book={book} />
                </div>
                <div className="flex min-w-0 flex-1 flex-col p-3">
                  <h2 className="line-clamp-2 leading-tight font-semibold">
                    {book.title || 'Untitled'}
                  </h2>
                  <p className="mt-1 truncate text-xs opacity-70">
                    {book.author || 'Unknown author'}
                  </p>
                  {book.year && <p className="text-xs opacity-60">{book.year}</p>}
                  {book.source_url && (
                    <a
                      href={book.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-1 text-xs text-emerald-700 underline dark:text-emerald-300"
                    >
                      View source
                    </a>
                  )}
                  {book.display_fields && (
                    <div className="mt-auto flex flex-wrap gap-2 text-[11px] opacity-70">
                      {book.display_fields.map((field) => (
                        <span key={`${field.label}-${field.value}`}>{field.value}</span>
                      ))}
                    </div>
                  )}
                  <div className="mt-2">
                    {book.in_my_library && book.book_id ? (
                      <Link
                        to={`/library/${book.book_id}`}
                        className="block rounded-sm bg-emerald-600 px-3 py-1.5 text-center text-xs font-semibold text-white hover:bg-emerald-700"
                      >
                        In Library
                      </Link>
                    ) : (
                      <button
                        type="button"
                        disabled={addingId === book.id}
                        onClick={() => void addBookToLibrary(book)}
                        className="w-full rounded-sm bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-60"
                      >
                        {addingId === book.id ? 'Adding...' : '+ Add'}
                      </button>
                    )}
                  </div>
                </div>
              </article>
            ))}
          </div>
          {hasMore && (
            <div className="mt-8 text-center">
              <button
                type="button"
                disabled={loadingMore}
                onClick={() => void runSearch(page + 1, true)}
                className="rounded-md border border-violet-500 px-4 py-2 text-sm font-semibold text-violet-700 disabled:opacity-60 dark:text-violet-300"
              >
                {loadingMore ? 'Loading...' : 'Load more'}
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
};
