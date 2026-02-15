import { useMemo, useEffect, CSSProperties } from 'react';
import { MultiSelectFieldConfig, TableFieldConfig, TableFieldColumn } from '../../../types/settings';
import { DropdownList } from '../../DropdownList';
import { MultiSelectField } from './MultiSelectField';

interface TableFieldProps {
  field: TableFieldConfig;
  value: Record<string, unknown>[];
  onChange: (value: Record<string, unknown>[]) => void;
  disabled?: boolean;
}

function defaultCellValue(column: TableFieldColumn): unknown {
  if (column.defaultValue !== undefined) {
    return column.defaultValue;
  }
  if (column.type === 'multiselect') {
    return [];
  }
  if (column.type === 'checkbox') {
    return false;
  }
  return '';
}

function normalizeMultiValue(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map((entry) => String(entry ?? '').trim())
      .filter((entry) => entry.length > 0);
  }
  if (typeof value === 'string') {
    const normalized = value.trim();
    return normalized ? [normalized] : [];
  }
  return [];
}

function normalizeRows(rows: Record<string, unknown>[], columns: TableFieldColumn[]): Record<string, unknown>[] {
  return (rows ?? []).map((row) => {
    const normalized: Record<string, unknown> = { ...row };
    for (const col of columns) {
      if (!(col.key in normalized)) {
        normalized[col.key] = defaultCellValue(col);
      }
    }
    return normalized;
  });
}

function getFilteredSelectOptions(
  column: TableFieldColumn,
  row: Record<string, unknown>
): Array<{ value: string; label: string; description?: string; childOf?: string }> {
  const options = (column.options ?? []).map((opt) => ({
    value: String(opt.value),
    label: opt.label ?? String(opt.value),
    description: opt.description,
    childOf:
      opt.childOf === undefined || opt.childOf === null
        ? undefined
        : String(opt.childOf),
  }));

  const filterByField = column.filterByField;
  if (!filterByField) {
    return options.filter((opt) => !opt.childOf);
  }

  const rawFilterValue = row[filterByField];
  const filterValue =
    rawFilterValue === undefined || rawFilterValue === null || rawFilterValue === ''
      ? undefined
      : String(rawFilterValue);

  if (!filterValue) {
    return options.filter((opt) => !opt.childOf);
  }

  return options.filter((opt) => !opt.childOf || opt.childOf === filterValue);
}

export const TableField = ({ field, value, onChange, disabled }: TableFieldProps) => {
  const isDisabled = disabled ?? false;

  const columns = useMemo(() => field.columns ?? [], [field.columns]);
  const rows = useMemo(() => normalizeRows(value ?? [], columns), [value, columns]);

  // Use minmax(0, ...) so the grid can shrink inside the settings modal.
  // Use fixed width for delete button column to ensure header/data alignment.
  const gridTemplate = 'sm:[grid-template-columns:var(--table-cols)]';

  const tableCols = useMemo(() => {
    if (columns.length === 0) {
      return 'minmax(0,1fr) 2rem';
    }

    const colDefs = columns.map((_, idx) => (idx === 0 ? 'minmax(0,180px)' : 'minmax(0,1fr)'));
    return `${colDefs.join(' ')} 2rem`;
  }, [columns]);

  const updateCell = (rowIndex: number, key: string, cellValue: unknown) => {
    const next = rows.map((row, idx) => (idx === rowIndex ? { ...row, [key]: cellValue } : row));
    onChange(next);
  };

  const addRow = () => {
    const newRow: Record<string, unknown> = {};
    columns.forEach((col) => {
      newRow[col.key] = defaultCellValue(col);
    });
    onChange([...(rows ?? []), newRow]);
  };

  const removeRow = (rowIndex: number) => {
    const next = rows.filter((_, idx) => idx !== rowIndex);
    onChange(next);
  };

  useEffect(() => {
    if (rows.length === 0) return;

    const nextRows = rows.map((row) => ({ ...row }));
    let hasChanges = false;

    rows.forEach((row, rowIndex) => {
      columns.forEach((col) => {
        if (col.type === 'multiselect') {
          const filteredOptions = getFilteredSelectOptions(col, row);
          const validValues = new Set(filteredOptions.map((opt) => opt.value));
          const currentValues = normalizeMultiValue(row[col.key]);
          const normalizedValues = currentValues.filter((entry) => validValues.has(entry));

          if (JSON.stringify(currentValues) !== JSON.stringify(normalizedValues)) {
            nextRows[rowIndex][col.key] = normalizedValues;
            hasChanges = true;
          }
          return;
        }

        if (col.type !== 'select') return;

        const filteredOptions = getFilteredSelectOptions(col, row);
        const currentValue = String(row[col.key] ?? '');
        const currentValueIsValid = filteredOptions.some((opt) => opt.value === currentValue);
        const nonEmptyOptions = filteredOptions.filter((opt) => opt.value !== '');

        if (nonEmptyOptions.length === 1) {
          const onlyOption = nonEmptyOptions[0].value;
          if (currentValue !== onlyOption) {
            nextRows[rowIndex][col.key] = onlyOption;
            hasChanges = true;
          }
          return;
        }

        if (currentValue && !currentValueIsValid) {
          nextRows[rowIndex][col.key] = '';
          hasChanges = true;
        }
      });
    });

    if (hasChanges) {
      onChange(nextRows);
    }
  }, [rows, columns, onChange]);

  if (rows.length === 0) {
    return (
      <div className="space-y-3">
        {field.emptyMessage && <p className="text-sm opacity-70">{field.emptyMessage}</p>}
        <button
          type="button"
          onClick={addRow}
          disabled={isDisabled}
          className="px-3 py-2 rounded-lg text-sm font-medium
                     bg-[var(--bg-soft)] border border-[var(--border-muted)]
                     hover-action transition-colors
                     disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {field.addLabel || 'Add'}
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3 min-w-0" style={{ '--table-cols': tableCols } as CSSProperties}>
      <div className={`hidden sm:grid ${gridTemplate} gap-3 items-start min-w-0 text-xs font-medium opacity-70`}>
        {columns.map((col) => (
          <div key={col.key} className="min-w-0 truncate">
            {col.label}
          </div>
        ))}
        <div />
      </div>

      <div className="space-y-3 min-w-0">
        {rows.map((row, rowIndex) => (
          <div
            key={rowIndex}
            className={`grid grid-cols-1 ${gridTemplate} gap-3 items-start min-w-0`}
            style={{ overflow: 'visible' }}
          >
            {columns.map((col) => {
              const cellValue = row[col.key];

              const mobileLabel = <div className="sm:hidden text-xs font-medium opacity-70">{col.label}</div>;

              if (col.type === 'checkbox') {
                return (
                  <div key={col.key} className="flex flex-col gap-1 min-w-0">
                    {mobileLabel}
                    <div className="pt-2">
                      <input
                        type="checkbox"
                        checked={Boolean(cellValue)}
                        onChange={(e) => updateCell(rowIndex, col.key, e.target.checked)}
                        disabled={isDisabled}
                        className="h-4 w-4 rounded border-gray-300 text-sky-600 focus:ring-sky-500
                                   disabled:opacity-60 disabled:cursor-not-allowed"
                      />
                    </div>
                  </div>
                );
              }

              if (col.type === 'select') {
                const options = getFilteredSelectOptions(col, row).map((opt) => ({
                  value: opt.value,
                  label: opt.label,
                  description: opt.description,
                }));

                return (
                  <div key={col.key} className="flex flex-col gap-1 min-w-0">
                    {mobileLabel}
                    {isDisabled ? (
                      <div className="w-full px-3 py-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] text-sm opacity-60 cursor-not-allowed">
                        {options.find((o) => o.value === String(cellValue ?? ''))?.label || 'Select...'}
                      </div>
                    ) : (
                      <DropdownList
                        options={options}
                        value={String(cellValue ?? '')}
                        onChange={(val) => updateCell(rowIndex, col.key, Array.isArray(val) ? val[0] : val)}
                        placeholder={col.placeholder || 'Select...'}
                        widthClassName="w-full"
                      />
                    )}
                  </div>
                );
              }

              if (col.type === 'multiselect') {
                const options = getFilteredSelectOptions(col, row).map((opt) => ({
                  value: opt.value,
                  label: opt.label,
                  description: opt.description,
                  childOf: opt.childOf,
                }));
                const selectedValues = normalizeMultiValue(cellValue).filter((entry) =>
                  options.some((option) => option.value === entry)
                );
                const multiSelectField: MultiSelectFieldConfig = {
                  type: 'MultiSelectField',
                  key: `${field.key}_${rowIndex}_${col.key}`,
                  label: col.label,
                  value: selectedValues,
                  options,
                  variant: 'dropdown',
                  placeholder: col.placeholder || 'Select...',
                };

                return (
                  <div key={col.key} className="flex flex-col gap-1 min-w-0">
                    {mobileLabel}
                    <MultiSelectField
                      field={multiSelectField}
                      value={selectedValues}
                      onChange={(nextValues) => {
                        const normalizedValues = (nextValues ?? [])
                          .map((entry) => String(entry ?? '').trim())
                          .filter((entry) => entry.length > 0);
                        updateCell(rowIndex, col.key, normalizedValues);
                      }}
                      disabled={isDisabled}
                    />
                  </div>
                );
              }

              // text/path
              return (
                <div key={col.key} className="flex flex-col gap-1 min-w-0">
                  {mobileLabel}
                  <input
                    type="text"
                    value={String(cellValue ?? '')}
                    onChange={(e) => updateCell(rowIndex, col.key, e.target.value)}
                    placeholder={col.placeholder}
                    disabled={isDisabled}
                    className="w-full px-3 py-2 rounded-lg border border-[var(--border-muted)]
                               bg-[var(--bg-soft)] text-sm
                               focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500
                               disabled:opacity-60 disabled:cursor-not-allowed
                               transition-colors"
                  />
                </div>
              );
            })}

            <div className="flex items-start pt-1.5">
              <button
                type="button"
                onClick={() => removeRow(rowIndex)}
                disabled={isDisabled}
                className="p-1.5 rounded-full hover-action
                           disabled:opacity-60 disabled:cursor-not-allowed"
                aria-label="Remove row"
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
            </div>

            <div className="col-span-full border-t border-[var(--border-muted)] opacity-60" />
          </div>
        ))}
      </div>

      <button
        type="button"
        onClick={addRow}
        disabled={isDisabled}
        className="px-3 py-2 rounded-lg text-sm font-medium
                   bg-[var(--bg-soft)] border border-[var(--border-muted)]
                   hover-action transition-colors
                   disabled:opacity-60 disabled:cursor-not-allowed"
      >
        {field.addLabel || 'Add'}
      </button>
    </div>
  );
};
