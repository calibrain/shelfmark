import { useState } from 'react';

import type { RequestRecord } from '../../types';
import { getRequestBookIdentity, type RequestBookIdentity } from './activityMappers';
import type { ActivityItem } from './activityTypes';

export interface RequestBookGroup {
  book: RequestBookIdentity;
  requests: ActivityItem[];
}

export const groupPendingRequestsByBook = (items: ActivityItem[]): RequestBookGroup[] => {
  const groups = new Map<string, RequestBookGroup>();

  items.forEach((item) => {
    const record = item.requestRecord;
    if (!record || record.status !== 'pending') return;

    const book = getRequestBookIdentity(record);
    const group = groups.get(book.id);
    if (group) {
      group.requests.push(item);
    } else {
      groups.set(book.id, { book, requests: [item] });
    }
  });

  return Array.from(groups.values()).toSorted(
    (left, right) =>
      Math.max(...right.requests.map((item) => item.timestamp)) -
      Math.max(...left.requests.map((item) => item.timestamp)),
  );
};

interface RequestBookGroupsProps {
  items: ActivityItem[];
  onFindRelease: (requestId: number, record: RequestRecord) => Promise<void> | void;
  onReject: (requestId: number) => Promise<void> | void;
}

const CoverFallback = () => (
  <div className="flex h-18 w-12 items-center justify-center rounded-sm bg-gray-200 text-[8px] font-medium text-gray-500 dark:bg-gray-700 dark:text-gray-400">
    No Cover
  </div>
);

const RequestBookGroupCard = ({
  group,
  onFindRelease,
  onReject,
}: {
  group: RequestBookGroup;
} & Omit<RequestBookGroupsProps, 'items'>) => {
  const [expanded, setExpanded] = useState(false);
  const [selectedRequestId, setSelectedRequestId] = useState<number | null>(null);
  const leader = group.requests[0]?.requestRecord;
  const selected = group.requests.find(
    (item) => item.requestId === selectedRequestId,
  )?.requestRecord;

  if (!leader) return null;

  return (
    <article className="rounded-lg border border-(--border-muted) p-3">
      <div className="flex items-start gap-3">
        <div className="h-18 w-12 shrink-0 overflow-hidden rounded-sm bg-gray-200 dark:bg-gray-700">
          {group.book.coverUrl ? (
            <img
              src={group.book.coverUrl}
              alt={`${group.book.title} cover`}
              className="h-full w-full object-cover object-top"
            />
          ) : (
            <CoverFallback />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-tight">
            <span className="font-semibold">{group.book.title}</span>
            {group.book.author && (
              <span className="text-xs opacity-60"> - {group.book.author}</span>
            )}
          </p>
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="mt-1 text-xs text-sky-600 hover:underline dark:text-sky-400"
            aria-expanded={expanded}
          >
            {group.requests.length} requester{group.requests.length === 1 ? '' : 's'}
            {expanded ? ' (hide)' : ' (show)'}
          </button>
        </div>
      </div>

      {expanded && (
        <div className="mt-3 space-y-1 border-t border-(--border-muted) pt-2">
          {group.requests.map((item) => {
            const record = item.requestRecord;
            if (!record || typeof item.requestId !== 'number') return null;
            const isSelected = selectedRequestId === item.requestId;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setSelectedRequestId(record.id)}
                className={`flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-xs ${isSelected ? 'bg-sky-500/10 text-sky-700 dark:text-sky-300' : 'hover:bg-(--hover-surface)'}`}
              >
                <span>{record.username?.trim() || `User ${record.user_id}`}</span>
                {isSelected && <span>Selected</span>}
              </button>
            );
          })}
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => void onFindRelease(leader.id, leader)}
          className="rounded-md bg-sky-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-sky-700"
        >
          Find release
        </button>
        {selected && (
          <button
            type="button"
            onClick={() => void onReject(selected.id)}
            className="rounded-md border border-red-300 px-2.5 py-1.5 text-xs text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950/30"
          >
            Reject selected
          </button>
        )}
      </div>
    </article>
  );
};

export const RequestBookGroups = ({ items, onFindRelease, onReject }: RequestBookGroupsProps) => (
  <div className="space-y-3">
    {groupPendingRequestsByBook(items).map((group) => (
      <RequestBookGroupCard
        key={group.book.id}
        group={group}
        onFindRelease={onFindRelease}
        onReject={onReject}
      />
    ))}
  </div>
);
