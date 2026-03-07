import { useCallback, useEffect, useMemo, useState } from 'react';
import { DropdownList, type DropdownListOption } from './DropdownList';
import {
  setBookTargetState,
  type BookTargetOption,
} from '../services/api';
import { loadBookTargets } from '../utils/bookTargetLoader';
import { emitBookTargetChange, onBookTargetChange } from '../utils/bookTargetEvents';

interface BookTargetDropdownProps {
  provider: string;
  bookId: string;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  widthClassName?: string;
  variant?: 'default' | 'pill' | 'icon';
  align?: 'left' | 'right' | 'auto';
  className?: string;
  onOpenChange?: (isOpen: boolean) => void;
}

const stripCountSuffix = (label: string): string => {
  return label.replace(/\s+\(\d+\)\s*$/, '');
};

const BookmarkIcon = ({ className = 'h-4 w-4' }: { className?: string }) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    fill="none"
    viewBox="0 0 24 24"
    strokeWidth="1.5"
    stroke="currentColor"
    aria-hidden="true"
    className={`${className} flex-shrink-0`}
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M17.593 3.322c1.1.128 1.907 1.077 1.907 2.185V21L12 17.25 4.5 21V5.507c0-1.108.806-2.057 1.907-2.185a48.507 48.507 0 0 1 11.186 0Z"
    />
  </svg>
);

const renderSummary = (selectedOptions: DropdownListOption[]) => {
  const count = selectedOptions.length;

  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <BookmarkIcon />
      <span>Hardcover Lists{count > 0 ? ` (${count})` : ''}</span>
    </span>
  );
};

const updateOptionChecked = (
  prev: BookTargetOption[],
  target: string,
  checked: boolean,
): BookTargetOption[] =>
  prev.map((option) =>
    option.value === target ? { ...option, checked } : option,
  );

export const BookTargetDropdown = ({
  provider,
  bookId,
  onShowToast,
  widthClassName = 'w-full sm:w-56',
  variant = 'default',
  align = 'auto',
  className,
  onOpenChange,
}: BookTargetDropdownProps) => {
  const [options, setOptions] = useState<BookTargetOption[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pendingTargets, setPendingTargets] = useState<Set<string>>(new Set());

  useEffect(() => {
    let isMounted = true;

    const run = async () => {
      try {
        const loaded = await loadBookTargets(provider, bookId);
        if (!isMounted) return;
        setOptions(loaded);
        setLoadError(null);
      } catch (error) {
        if (!isMounted) return;
        const message = error instanceof Error ? error.message : 'Failed to load Hardcover lists';
        setOptions([]);
        setLoadError(message);
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    };

    setLoadError(null);
    setPendingTargets(new Set());
    setIsLoading(true);
    void run();

    return () => {
      isMounted = false;
    };
  }, [provider, bookId]);

  // Sync from changes made by other BookTargetDropdown instances for the same book
  useEffect(() => {
    return onBookTargetChange((event) => {
      if (event.provider !== provider || event.bookId !== bookId) return;
      setOptions((prev) => updateOptionChecked(prev, event.target, event.selected));
    });
  }, [provider, bookId]);

  const selectedValues = useMemo(
    () => options.filter((option) => option.checked).map((option) => option.value),
    [options],
  );

  const dropdownOptions = useMemo<DropdownListOption[]>(() => {
    if (isLoading) {
      return [{ value: '__loading', label: 'Loading…', disabled: true }];
    }

    if (loadError) {
      return [{ value: '__error', label: loadError, disabled: true }];
    }

    if (options.length === 0) {
      return [{ value: '__empty', label: 'No writable Hardcover targets', disabled: true }];
    }

    return options.map((option) => ({
      value: option.value,
      label: option.label,
      description: option.description,
      disabled: !option.writable || pendingTargets.has(option.value),
    }));
  }, [isLoading, loadError, options, pendingTargets]);

  const handleChange = useCallback((nextValue: string[] | string) => {
    if (!Array.isArray(nextValue)) {
      return;
    }

    const nextSelected = new Set(nextValue);
    const currentSelected = new Set(selectedValues);
    const toggledTarget =
      nextValue.find((value) => !currentSelected.has(value))
      ?? selectedValues.find((value) => !nextSelected.has(value));

    if (!toggledTarget || pendingTargets.has(toggledTarget)) {
      return;
    }

    const selected = nextSelected.has(toggledTarget);
    const toggledOption = options.find((option) => option.value === toggledTarget);
    if (!toggledOption) {
      return;
    }

    setPendingTargets((prev) => new Set(prev).add(toggledTarget));
    setOptions((prev) => updateOptionChecked(prev, toggledTarget, selected));

    void (async () => {
      try {
        const result = await setBookTargetState(provider, bookId, toggledTarget, selected);
        setOptions((prev) => updateOptionChecked(prev, toggledTarget, result.selected));

        if (result.changed) {
          emitBookTargetChange({
            provider,
            bookId,
            target: toggledTarget,
            selected: result.selected,
          });
          const label = stripCountSuffix(toggledOption.label);
          onShowToast?.(
            `${result.selected ? 'Added to' : 'Removed from'} ${label}`,
            'success',
          );
        }
      } catch (error) {
        setOptions((prev) => updateOptionChecked(prev, toggledTarget, !selected));
        const message = error instanceof Error ? error.message : 'Failed to update Hardcover list';
        onShowToast?.(message, 'error');
      } finally {
        setPendingTargets((prev) => {
          const nextPending = new Set(prev);
          nextPending.delete(toggledTarget);
          return nextPending;
        });
      }
    })();
  }, [bookId, onShowToast, options, pendingTargets, provider, selectedValues]);

  const customTrigger = variant === 'pill'
    ? ({ toggle }: { isOpen: boolean; toggle: () => void }) => {
        const count = selectedValues.length;
        return (
          <button
            type="button"
            onClick={toggle}
            className={`inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full transition-colors text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 hover:bg-emerald-100 dark:hover:bg-emerald-900/40 focus:outline-none`}
          >
            <BookmarkIcon className="w-3 h-3" />
            Hardcover Lists{count > 0 ? ` (${count})` : ''}
          </button>
        );
      }
    : variant === 'icon'
    ? ({ toggle }: { isOpen: boolean; toggle: () => void }) => {
        const count = selectedValues.length;
        return (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); toggle(); }}
            className={`flex items-center justify-center rounded-full transition-all duration-200 focus:outline-none ${className ?? 'p-1.5 sm:p-2 text-gray-600 dark:text-gray-200 hover-action'}`}
            aria-label="Hardcover Lists"
            title={count > 0 ? `On ${count} Hardcover list${count > 1 ? 's' : ''}` : 'Hardcover Lists'}
          >
            <BookmarkIcon className={`w-4 h-4 sm:w-5 sm:h-5 ${count > 0 ? 'fill-current' : ''}`} />
          </button>
        );
      }
    : undefined;

  return (
    <DropdownList
      options={dropdownOptions}
      value={selectedValues}
      onChange={handleChange}
      placeholder={isLoading ? 'Loading…' : 'Lists & Want to Read'}
      widthClassName={variant !== 'default' ? 'w-auto' : widthClassName}
      buttonClassName={variant !== 'default' ? '' : 'py-1.5 leading-none'}
      panelClassName={variant !== 'default' ? 'w-56' : undefined}
      align={align}
      multiple
      showCheckboxes
      keepOpenOnSelect
      summaryFormatter={(selectedOptions) => renderSummary(selectedOptions)}
      renderTrigger={customTrigger}
      onOpenChange={onOpenChange}
    />
  );
};
