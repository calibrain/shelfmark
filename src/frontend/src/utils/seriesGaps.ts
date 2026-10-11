import type {
  SeriesGapFormat,
  SeriesGapRequestItem,
  SeriesGapRequestResult,
  SeriesGapSeries,
  SeriesGapVolume,
} from '../services/api';

export type SeriesGapSelection = Record<string, SeriesGapRequestItem>;

export const selectionKey = (item: SeriesGapRequestItem): string =>
  `${item.series_id}:${item.content_type}:${item.provider_id}`;

export const volumeItem = (
  series: SeriesGapSeries,
  volume: SeriesGapVolume,
  format: SeriesGapFormat,
): SeriesGapRequestItem | null => {
  if (!series.hardcover) {
    return null;
  }
  return { series_id: series.hardcover.id, provider_id: volume.provider_id, content_type: format };
};

const SERIES_GAP_FORMATS: SeriesGapFormat[] = ['ebook', 'audiobook'];

/** Every (volume, format) pair the report says is missing, in series order. */
export const missingItems = (series: SeriesGapSeries): SeriesGapRequestItem[] => {
  const items: SeriesGapRequestItem[] = [];
  series.volumes.forEach((volume) => {
    SERIES_GAP_FORMATS.forEach((format) => {
      if (volume.statuses[format] !== 'missing') {
        return;
      }
      const item = volumeItem(series, volume, format);
      if (item) {
        items.push(item);
      }
    });
  });
  return items;
};

export const seriesHasGaps = (series: SeriesGapSeries): boolean =>
  Object.values(series.formats).some((summary) => summary.missing > 0);

export const withItems = (
  selection: SeriesGapSelection,
  items: SeriesGapRequestItem[],
  selected: boolean,
): SeriesGapSelection => {
  const next = { ...selection };
  items.forEach((item) => {
    const key = selectionKey(item);
    if (selected) {
      next[key] = item;
    } else {
      delete next[key];
    }
  });
  return next;
};

export const toggleItem = (
  selection: SeriesGapSelection,
  item: SeriesGapRequestItem,
): SeriesGapSelection => withItems(selection, [item], !(selectionKey(item) in selection));

export const formatPosition = (position: number | null): string => {
  if (position === null || Number.isNaN(position)) {
    return '?';
  }
  return Number.isInteger(position) ? String(position) : position.toFixed(1).replace(/0$/, '');
};

const plural = (count: number, noun: string): string => `${count} ${noun}${count === 1 ? '' : 's'}`;

/** One line for a toast: what was requested, what was skipped and what failed. */
export const summarizeRequestResult = (result: SeriesGapRequestResult): string => {
  const parts = [`Requested ${plural(result.created, 'volume')}`];
  const alreadyRequested = result.items.filter((item) => item.result === 'requested').length;
  const owned = result.items.filter((item) => item.result === 'owned').length;
  if (alreadyRequested > 0) {
    parts.push(`${alreadyRequested} already requested`);
  }
  if (owned > 0) {
    parts.push(`${owned} already in the library`);
  }
  if (result.errors > 0) {
    parts.push(plural(result.errors, 'error'));
  }
  return parts.join(', ');
};
