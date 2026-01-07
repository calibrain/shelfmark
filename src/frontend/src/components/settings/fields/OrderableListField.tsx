import { useState, useRef } from 'react';
import {
  OrderableListFieldConfig,
  OrderableListItem,
  OrderableListOption,
} from '../../../types/settings';

interface OrderableListFieldProps {
  field: OrderableListFieldConfig;
  value: OrderableListItem[];
  onChange: (value: OrderableListItem[]) => void;
  disabled?: boolean;
}

// Represents where the drop indicator should appear
type DropPosition = { index: number; position: 'before' | 'after' } | null;

// Merged item type with all properties
type MergedItem = OrderableListItem & OrderableListOption;

/**
 * Merge current value with options to get full item info.
 * Items in value take precedence; any options not in value are appended.
 */
const mergeValueWithOptions = (
  value: OrderableListItem[],
  options: OrderableListOption[]
): MergedItem[] => {
  const optionsMap = new Map(options.map((opt) => [opt.id, opt]));
  const result: MergedItem[] = [];

  // Add items from value (preserves order)
  for (const item of value) {
    const option = optionsMap.get(item.id);
    if (option) {
      result.push({ ...option, ...item });
      optionsMap.delete(item.id);
    }
  }

  // Add any remaining options not in value
  for (const option of optionsMap.values()) {
    result.push({ ...option, id: option.id, enabled: false });
  }

  return result;
};

export const OrderableListField = ({
  field,
  value,
  onChange,
  disabled,
}: OrderableListFieldProps) => {
  const isDisabled = disabled ?? false;
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
  const [dropPosition, setDropPosition] = useState<DropPosition>(null);
  const dragNodeRef = useRef<HTMLDivElement | null>(null);

  const items = mergeValueWithOptions(value ?? [], field.options);

  // Check if a move is valid (not crossing pinned items)
  const isValidMove = (fromIndex: number): boolean => {
    const fromItem = items[fromIndex];
    if (fromItem?.isPinned) return false;
    return true;
  };

  const handleDragStart = (e: React.DragEvent, index: number) => {
    const item = items[index];
    if (item?.isPinned) {
      e.preventDefault();
      return;
    }

    setDraggedIndex(index);
    dragNodeRef.current = e.currentTarget as HTMLDivElement;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(index));
    requestAnimationFrame(() => {
      if (dragNodeRef.current) {
        dragNodeRef.current.classList.add('opacity-50');
      }
    });
  };

  const handleDragEnd = () => {
    if (dragNodeRef.current) {
      dragNodeRef.current.classList.remove('opacity-50');
    }
    setDraggedIndex(null);
    setDropPosition(null);
    dragNodeRef.current = null;
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    if (draggedIndex === null || draggedIndex === index) {
      setDropPosition(null);
      return;
    }

    // Can't drop on pinned items
    if (items[index]?.isPinned) {
      setDropPosition(null);
      return;
    }

    const rect = e.currentTarget.getBoundingClientRect();
    const midpoint = rect.top + rect.height / 2;
    const position = e.clientY < midpoint ? 'before' : 'after';

    setDropPosition({ index, position });
  };

  const handleDragLeave = (e: React.DragEvent) => {
    const relatedTarget = e.relatedTarget as Node | null;
    if (!e.currentTarget.contains(relatedTarget)) {
      setDropPosition(null);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (draggedIndex === null || dropPosition === null) {
      handleDragEnd();
      return;
    }

    let targetIndex = dropPosition.index;
    if (dropPosition.position === 'after') {
      targetIndex += 1;
    }
    if (draggedIndex < targetIndex) {
      targetIndex -= 1;
    }

    if (draggedIndex === targetIndex || !isValidMove(draggedIndex)) {
      handleDragEnd();
      return;
    }

    const newItems = [...items];
    const [removed] = newItems.splice(draggedIndex, 1);
    newItems.splice(targetIndex, 0, removed);

    const newValue: OrderableListItem[] = newItems.map((item) => ({
      id: item.id,
      enabled: item.enabled,
    }));

    onChange(newValue);
    handleDragEnd();
  };

  const toggleItem = (index: number) => {
    if (isDisabled) return;
    const item = items[index];
    if (item.isLocked) return;

    const newValue: OrderableListItem[] = items.map((it, i) => ({
      id: it.id,
      enabled: i === index ? !it.enabled : it.enabled,
    }));

    onChange(newValue);
  };

  const moveItem = (fromIndex: number, direction: 'up' | 'down') => {
    const toIndex = direction === 'up' ? fromIndex - 1 : fromIndex + 1;
    if (toIndex < 0 || toIndex >= items.length) return;
    if (!isValidMove(fromIndex)) return;

    const newItems = [...items];
    [newItems[fromIndex], newItems[toIndex]] = [newItems[toIndex], newItems[fromIndex]];

    const newValue: OrderableListItem[] = newItems.map((item) => ({
      id: item.id,
      enabled: item.enabled,
    }));

    onChange(newValue);
  };

  const canMoveUp = (index: number): boolean => {
    const item = items[index];
    if (item?.isPinned) return false;
    if (index === 0) return false;
    if (items[index - 1]?.isPinned) return false;
    return true;
  };

  const canMoveDown = (index: number): boolean => {
    const item = items[index];
    if (item?.isPinned) return false;
    if (index === items.length - 1) return false;
    return true;
  };

  const getDropGapIndex = (): number | null => {
    if (!dropPosition) return null;
    return dropPosition.position === 'before'
      ? dropPosition.index
      : dropPosition.index + 1;
  };

  const dropGapIndex = getDropGapIndex();

  return (
    <div className="flex flex-col gap-1">
      {items.map((item, index) => {
        const isDragging = draggedIndex === index;
        const isItemDisabled = isDisabled || item.isLocked;
        const isPinned = item.isPinned ?? false;
        const showIndicatorBefore = dropGapIndex === index;

        return (
          <div key={item.id} className="relative">
            {/* Drop indicator */}
            {showIndicatorBefore && (
              <div className="absolute left-1 right-1 h-1 bg-sky-500 rounded-full z-10 -top-1 -translate-y-1/2" />
            )}

            <div
              draggable={!isPinned}
              onDragStart={(e) => handleDragStart(e, index)}
              onDragEnd={handleDragEnd}
              onDragOver={(e) => handleDragOver(e, index)}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              className={`
                flex items-center gap-3 p-3 rounded-lg
                transition-all duration-150
                ${isDragging ? 'opacity-50 cursor-grabbing' : isPinned ? 'cursor-default' : 'cursor-grab'}
                border border-[var(--border-muted)]
                ${isDisabled ? 'opacity-60' : !isPinned ? 'hover:bg-[var(--hover-surface)]' : ''}
              `}
            >
              {/* Reorder Controls - hidden for pinned items */}
              {!isPinned ? (
                <div className="flex flex-col flex-shrink-0 -my-1">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      moveItem(index, 'up');
                    }}
                    disabled={!canMoveUp(index)}
                    className={`
                      p-1.5 sm:p-0.5 rounded transition-colors
                      ${!canMoveUp(index)
                        ? 'text-gray-300 dark:text-gray-600 cursor-not-allowed'
                        : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 sm:hover:bg-gray-100 sm:dark:hover:bg-gray-700'
                      }
                    `}
                    aria-label="Move up"
                  >
                    <svg className="w-5 h-5 sm:w-4 sm:h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      moveItem(index, 'down');
                    }}
                    disabled={!canMoveDown(index)}
                    className={`
                      p-1.5 sm:p-0.5 rounded transition-colors
                      ${!canMoveDown(index)
                        ? 'text-gray-300 dark:text-gray-600 cursor-not-allowed'
                        : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 sm:hover:bg-gray-100 sm:dark:hover:bg-gray-700'
                      }
                    `}
                    aria-label="Move down"
                  >
                    <svg className="w-5 h-5 sm:w-4 sm:h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>
                </div>
              ) : (
                <div className="w-5 sm:w-4 flex-shrink-0" />
              )}

              {/* Label and Description */}
              <div className="flex-1 min-w-0">
                <div className="font-medium text-sm">{item.label}</div>
                {item.description && (
                  <div className="text-xs text-[var(--text-muted)] mt-0.5">
                    {item.description}
                  </div>
                )}
                {item.isLocked && item.disabledReason && (
                  <div className="text-xs text-amber-500 mt-0.5 flex items-center gap-1">
                    <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                      <path
                        fillRule="evenodd"
                        d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z"
                        clipRule="evenodd"
                      />
                    </svg>
                    {item.disabledReason}
                  </div>
                )}
              </div>

              {/* Toggle Switch */}
              <button
                type="button"
                role="switch"
                aria-checked={item.enabled && !item.isLocked}
                onClick={(e) => {
                  e.stopPropagation();
                  toggleItem(index);
                }}
                disabled={isItemDisabled}
                className={`
                  relative inline-flex h-6 w-11 flex-shrink-0 items-center rounded-full
                  transition-colors duration-200 focus:outline-none focus:ring-2
                  focus:ring-sky-500/50 disabled:opacity-60 disabled:cursor-not-allowed
                  ${item.enabled && !item.isLocked ? 'bg-sky-600' : 'bg-gray-300 dark:bg-gray-600'}
                `}
              >
                <span
                  className={`
                    inline-block h-4 w-4 transform rounded-full bg-white
                    shadow-sm transition-transform duration-200
                    ${item.enabled && !item.isLocked ? 'translate-x-6' : 'translate-x-1'}
                  `}
                />
              </button>
            </div>
          </div>
        );
      })}
      {/* Drop indicator after last item */}
      {dropGapIndex === items.length && (
        <div className="relative h-0">
          <div className="absolute left-1 right-1 h-1 bg-sky-500 rounded-full z-10 -top-0.5" />
        </div>
      )}
    </div>
  );
};
