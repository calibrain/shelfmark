import { useCallback, useMemo, useRef, useState } from 'react';

import { useBodyScrollLock } from '../../hooks/useBodyScrollLock';
import { useEscapeKey } from '../../hooks/useEscapeKey';
import { useMountEffect } from '../../hooks/useMountEffect';
import type {
  SeriesGapFormat,
  SeriesGapReport,
  SeriesGapRequestItem,
  SeriesGapSeries,
  SeriesGapStatus,
  SeriesGapVolume,
} from '../../services/api';
import { getSeriesGapReport, requestSeriesGapVolumes } from '../../services/api';
import type { SeriesGapSelection } from '../../utils/seriesGaps';
import {
  formatPosition,
  missingItems,
  selectionKey,
  seriesHasGaps,
  summarizeRequestResult,
  toggleItem,
  volumeItem,
  withItems,
} from '../../utils/seriesGaps';

type ToastType = 'success' | 'error' | 'info';

interface SeriesGapModalProps {
  isOpen: boolean;
  onClose: () => void;
  onShowToast?: (message: string, type: ToastType) => void;
  // Called after at least one request was created, so the activity view can refresh.
  onRequested?: () => void;
}

const TITLE_ID = 'series-gap-modal-title';

const FORMAT_LABELS: Record<SeriesGapFormat, string> = {
  ebook: 'Ebook',
  audiobook: 'Audiobook',
};

const STATUS_LABELS: Record<SeriesGapStatus, string> = {
  owned: 'Owned',
  collection: 'In a collection',
  requested: 'Requested',
  missing: 'Missing',
};

const STATUS_CLASSES: Record<SeriesGapStatus, string> = {
  owned: 'text-green-700 dark:text-green-300',
  collection: 'text-green-700 dark:text-green-300',
  requested: 'text-amber-700 dark:text-amber-300',
  missing: 'text-red-700 dark:text-red-300',
};

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
};

const formatSummary = (series: SeriesGapSeries, format: SeriesGapFormat): string => {
  const summary = series.formats[format];
  if (!summary || !summary.collected) {
    return `${FORMAT_LABELS[format]}: not collected`;
  }
  const parts = [`${summary.owned} owned`];
  if (summary.requested > 0) {
    parts.push(`${summary.requested} requested`);
  }
  parts.push(`${summary.missing} missing`);
  return `${FORMAT_LABELS[format]}: ${parts.join(', ')}`;
};

const seriesSubtitle = (series: SeriesGapSeries, formats: SeriesGapFormat[]): string => {
  const parts: string[] = [];
  if (series.author) {
    parts.push(series.author);
  }
  if (series.hardcover && series.hardcover.name !== series.name) {
    parts.push(`Hardcover: ${series.hardcover.name}`);
  }
  if (series.status === 'ok') {
    formats.forEach((format) => {
      parts.push(formatSummary(series, format));
    });
  }
  return parts.join(' · ');
};

const reportSummary = (report: SeriesGapReport): string => {
  const withGaps = report.series.filter(seriesHasGaps).length;
  const parts = [`${report.series.length} series`, `${withGaps} with gaps`];
  if (report.pending > 0) {
    parts.push(`${report.pending} still loading`);
  }
  return parts.join(', ');
};

const isVisible = (series: SeriesGapSeries, onlyGaps: boolean): boolean =>
  !onlyGaps || series.status !== 'ok' || seriesHasGaps(series);

interface StatusCellProps {
  series: SeriesGapSeries;
  volume: SeriesGapVolume;
  format: SeriesGapFormat;
  selection: SeriesGapSelection;
  onToggle: (item: SeriesGapRequestItem) => void;
}

const StatusCell = ({ series, volume, format, selection, onToggle }: StatusCellProps) => {
  const status = volume.statuses[format];
  if (status === undefined || status === null) {
    return <td className="py-1 pr-3 opacity-40">-</td>;
  }
  const item = status === 'missing' ? volumeItem(series, volume, format) : null;
  if (!item) {
    return <td className={`py-1 pr-3 ${STATUS_CLASSES[status]}`}>{STATUS_LABELS[status]}</td>;
  }
  return (
    <td className="py-1 pr-3">
      <label
        className={`inline-flex cursor-pointer items-center gap-1.5 ${STATUS_CLASSES.missing}`}
      >
        <input
          type="checkbox"
          className="h-4 w-4 accent-sky-600"
          checked={selectionKey(item) in selection}
          onChange={() => onToggle(item)}
          aria-label={`Request the ${FORMAT_LABELS[format].toLowerCase()} of ${volume.title}`}
        />
        {STATUS_LABELS.missing}
      </label>
    </td>
  );
};

interface SeriesSectionProps {
  series: SeriesGapSeries;
  formats: SeriesGapFormat[];
  selection: SeriesGapSelection;
  onToggle: (item: SeriesGapRequestItem) => void;
  onSelectAll: (series: SeriesGapSeries, selected: boolean) => void;
}

const SeriesSection = ({
  series,
  formats,
  selection,
  onToggle,
  onSelectAll,
}: SeriesSectionProps) => {
  const missing = missingItems(series);
  const allSelected =
    missing.length > 0 && missing.every((item) => selectionKey(item) in selection);

  return (
    <section className="border-b border-(--border-muted) py-4 last:border-b-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="min-w-0">
          <h4 className="text-sm font-semibold">{series.name}</h4>
          <p className="text-xs opacity-60">{seriesSubtitle(series, formats)}</p>
        </div>
        {missing.length > 0 && (
          <button
            type="button"
            className="text-xs text-sky-600 hover:underline dark:text-sky-400"
            onClick={() => onSelectAll(series, !allSelected)}
          >
            {allSelected ? 'Clear selection' : `Select all missing (${missing.length})`}
          </button>
        )}
      </div>
      {series.message && (
        <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">{series.message}</p>
      )}
      {series.status === 'pending' && (
        <p className="mt-1 text-xs opacity-60">Asking Hardcover...</p>
      )}
      {series.volumes.length > 0 && (
        <table className="mt-2 w-full border-collapse text-sm">
          <thead>
            <tr className="text-left text-xs uppercase opacity-60">
              <th className="py-1 pr-3 font-medium">#</th>
              <th className="py-1 pr-3 font-medium">Title</th>
              <th className="py-1 pr-3 font-medium">Year</th>
              {formats.map((format) => (
                <th key={format} className="py-1 pr-3 font-medium">
                  {FORMAT_LABELS[format]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {series.volumes.map((volume) => (
              <tr key={volume.provider_id} className="align-top">
                <td className="py-1 pr-3 whitespace-nowrap tabular-nums opacity-70">
                  {formatPosition(volume.position)}
                </td>
                <td className="py-1 pr-3">{volume.title}</td>
                <td className="py-1 pr-3 tabular-nums opacity-70">{volume.year ?? ''}</td>
                {formats.map((format) => (
                  <StatusCell
                    key={format}
                    series={series}
                    volume={volume}
                    format={format}
                    selection={selection}
                    onToggle={onToggle}
                  />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
};

export const SeriesGapModal = ({
  isOpen,
  onClose,
  onShowToast,
  onRequested,
}: SeriesGapModalProps) => {
  const [isClosing, setIsClosing] = useState(false);
  const [sessionVersion, setSessionVersion] = useState(0);

  const handleRequestClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
      setSessionVersion((current) => current + 1);
    }, 150);
  }, [onClose]);

  useBodyScrollLock(isOpen);

  if (!isOpen && !isClosing) {
    return null;
  }

  return (
    <SeriesGapModalSession
      key={sessionVersion}
      isOpen={isOpen}
      isClosing={isClosing}
      onRequestClose={handleRequestClose}
      onShowToast={onShowToast}
      onRequested={onRequested}
    />
  );
};

interface SeriesGapModalSessionProps {
  isOpen: boolean;
  isClosing: boolean;
  onRequestClose: () => void;
  onShowToast?: (message: string, type: ToastType) => void;
  onRequested?: () => void;
}

const SeriesGapModalSession = ({
  isOpen,
  isClosing,
  onRequestClose,
  onShowToast,
  onRequested,
}: SeriesGapModalSessionProps) => {
  const [report, setReport] = useState<SeriesGapReport | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selection, setSelection] = useState<SeriesGapSelection>({});
  const [onlyGaps, setOnlyGaps] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  // Set when the session unmounts, so a load still polling for pending series stops.
  const cancelledRef = useRef(false);

  const load = useCallback(async (refresh: boolean) => {
    setIsLoading(true);
    setLoadError(null);
    // The server answers within its time budget and says how many series it did not
    // reach; ask again until it has reached them all.
    const fetchUntilComplete = async (askAgain: boolean): Promise<void> => {
      const next = await getSeriesGapReport(askAgain);
      if (cancelledRef.current) {
        return;
      }
      setReport(next);
      if (next.pending > 0) {
        await fetchUntilComplete(false);
      }
    };
    try {
      await fetchUntilComplete(refresh);
    } catch (error) {
      if (!cancelledRef.current) {
        setLoadError(getErrorMessage(error, 'Failed to load the series report'));
      }
    } finally {
      if (!cancelledRef.current) {
        setIsLoading(false);
      }
    }
  }, []);

  useMountEffect(() => {
    void load(false);
    return () => {
      cancelledRef.current = true;
    };
  });

  const handleClose = useCallback(() => {
    if (isSubmitting) {
      return;
    }
    onRequestClose();
  }, [isSubmitting, onRequestClose]);

  useEscapeKey(isOpen, handleClose);

  const selectedItems = useMemo(() => Object.values(selection), [selection]);
  const visibleSeries = useMemo(
    () => (report ? report.series.filter((series) => isVisible(series, onlyGaps)) : []),
    [onlyGaps, report],
  );

  const handleToggle = useCallback((item: SeriesGapRequestItem) => {
    setSelection((current) => toggleItem(current, item));
  }, []);

  const handleSelectAll = useCallback((series: SeriesGapSeries, selected: boolean) => {
    setSelection((current) => withItems(current, missingItems(series), selected));
  }, []);

  const handleRequestSelected = useCallback(async () => {
    if (selectedItems.length === 0) {
      return;
    }
    setIsSubmitting(true);
    try {
      const result = await requestSeriesGapVolumes(selectedItems);
      onShowToast?.(summarizeRequestResult(result), result.errors > 0 ? 'error' : 'success');
      setSelection({});
      if (result.created > 0) {
        onRequested?.();
      }
      await load(false);
    } catch (error) {
      onShowToast?.(getErrorMessage(error, 'Failed to request the selected volumes'), 'error');
    } finally {
      setIsSubmitting(false);
    }
  }, [load, onRequested, onShowToast, selectedItems]);

  const renderBody = () => {
    if (!report) {
      if (loadError) {
        return (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <p className="text-sm opacity-70">{loadError}</p>
            <button
              type="button"
              onClick={() => {
                void load(false);
              }}
              className="rounded-lg border border-(--border-muted) px-4 py-2 text-sm font-medium transition-colors hover:bg-(--hover-surface)"
            >
              Try again
            </button>
          </div>
        );
      }
      return (
        <div className="flex h-full items-center justify-center text-sm opacity-60">
          Reading the libraries and asking Hardcover...
        </div>
      );
    }
    if (report.formats.length === 0) {
      return (
        <div className="flex h-full items-center justify-center text-center text-sm opacity-70">
          No library is connected. Turn on the library check under Settings, Hardcover Sync, to see
          which series you collect.
        </div>
      );
    }
    if (visibleSeries.length === 0) {
      return (
        <div className="flex h-full items-center justify-center text-sm opacity-60">
          {onlyGaps
            ? 'No gaps in the series you collect.'
            : 'No series found in the connected libraries.'}
        </div>
      );
    }
    return (
      <>
        {loadError && <p className="mb-2 text-xs text-red-700 dark:text-red-300">{loadError}</p>}
        {visibleSeries.map((series) => (
          <SeriesSection
            key={series.key}
            series={series}
            formats={report.formats}
            selection={selection}
            onToggle={handleToggle}
            onSelectAll={handleSelectAll}
          />
        ))}
      </>
    );
  };

  const requestLabel =
    selectedItems.length > 0 ? `Request selected (${selectedItems.length})` : 'Request selected';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        className={`absolute inset-0 bg-black/60 transition-opacity duration-150 ${isClosing ? 'opacity-0' : 'opacity-100'}`}
        onClick={handleClose}
        tabIndex={-1}
        aria-label="Close series gaps"
      />

      <div
        className={`relative flex h-[85vh] max-h-[750px] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-(--border-muted) shadow-2xl ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        style={{ background: 'var(--bg)' }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={TITLE_ID}
      >
        <header className="flex items-center justify-between gap-3 border-b border-(--border-muted) px-6 py-4">
          <div className="min-w-0">
            <h3 id={TITLE_ID} className="text-sm font-medium">
              Series gaps
            </h3>
            <p className="text-xs opacity-60">
              {report
                ? reportSummary(report)
                : 'Which volumes of the series you collect are missing'}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <label className="flex cursor-pointer items-center gap-1.5 text-xs">
              <input
                type="checkbox"
                className="h-4 w-4 accent-sky-600"
                checked={onlyGaps}
                onChange={(event) => setOnlyGaps(event.target.checked)}
              />
              Only series with gaps
            </label>
            <button
              type="button"
              onClick={() => {
                void load(true);
              }}
              disabled={isLoading || isSubmitting}
              className="rounded-lg border border-(--border-muted) px-3 py-1.5 text-xs font-medium transition-colors hover:bg-(--hover-surface) disabled:cursor-not-allowed disabled:opacity-50"
              title="Forget the cached Hardcover answers and ask again"
            >
              {isLoading ? 'Loading...' : 'Refresh'}
            </button>
            <button
              type="button"
              onClick={handleClose}
              className="hover-action rounded-full p-2 text-gray-500 transition-colors hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-50 dark:text-gray-400 dark:hover:text-gray-100"
              aria-label="Close series gaps"
              disabled={isSubmitting}
            >
              <svg
                className="h-5 w-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                strokeWidth={1.5}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto px-6 py-2">{renderBody()}</div>

        <footer className="flex items-center justify-between border-t border-(--border-muted) px-6 py-3">
          <span className="text-xs opacity-60">
            {selectedItems.length === 0
              ? 'Tick the missing volumes to request'
              : `${selectedItems.length} selected`}
          </span>
          <button
            type="button"
            onClick={() => {
              void handleRequestSelected();
            }}
            disabled={selectedItems.length === 0 || isSubmitting}
            className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isSubmitting ? 'Requesting...' : requestLabel}
          </button>
        </footer>
      </div>
    </div>
  );
};
