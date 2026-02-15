import { HeadingFieldConfig } from '../../../types/settings';

interface HeadingFieldProps {
  field: HeadingFieldConfig;
}

export const HeadingField = ({ field }: HeadingFieldProps) => (
  <div className="pb-1 [&:not(:first-child)]:pt-5 [&:not(:first-child)]:mt-1 [&:not(:first-child)]:border-t [&:not(:first-child)]:border-[var(--border-muted)]">
    <h3 className="text-base font-semibold mb-1">{field.title}</h3>
    {field.description && (
      <p className="text-sm opacity-70">
        {field.description}
        {field.linkUrl && (
          <>
            {' '}
            <a
              href={field.linkUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="underline text-sky-600 dark:text-sky-400"
            >
              {field.linkText || field.linkUrl}
            </a>
          </>
        )}
      </p>
    )}
  </div>
);
