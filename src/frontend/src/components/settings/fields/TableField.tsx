import { useMemo } from 'react';
import { TableFieldConfig, TableFieldColumn } from '../../../types/settings';
import { DropdownList } from '../../DropdownList';

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
  if (column.type === 'checkbox') {
    return false;
  }
  return '';
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

export const TableField = ({ field, value, onChange, disabled }: TableFieldProps) => {
  const isDisabled = disabled ?? false;

  const columns = useMemo(() => field.columns ?? [], [field.columns]);
  const rows = useMemo(() => normalizeRows(value ?? [], columns), [value, columns]);

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
                     hover:bg-[var(--hover-surface)] transition-colors
                     disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {field.addLabel || 'Add'}
        </button>
      </div>
    );
  }

  // Use minmax(0, ...) so the grid can shrink inside the settings modal.
  const gridTemplate = 'sm:grid-cols-[minmax(0,180px)_minmax(0,1fr)_minmax(0,1fr)_auto]';

  return (
    <div className="space-y-3 min-w-0">
      <div className={`hidden sm:grid ${gridTemplate} gap-2 px-1 text-xs font-medium opacity-70`}>
        {columns.map((col) => (
          <div key={col.key} className="truncate">
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
                const options = (col.options ?? []).map((opt) => ({
                  value: String(opt.value),
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

            <div className="flex items-center justify-center">
              <button
                type="button"
                onClick={() => removeRow(rowIndex)}
                disabled={isDisabled}
                className="p-2 rounded hover:bg-[var(--hover-surface)]
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
                   hover:bg-[var(--hover-surface)] transition-colors
                   disabled:opacity-60 disabled:cursor-not-allowed"
      >
        {field.addLabel || 'Add'}
      </button>
    </div>
  );
};
