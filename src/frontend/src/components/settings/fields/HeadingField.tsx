import { HeadingFieldConfig } from '../../../types/settings';

interface HeadingFieldProps {
  field: HeadingFieldConfig;
}

export const HeadingField = ({ field }: HeadingFieldProps) => (
  <div className="pb-1 not-first:pt-5 not-first:mt-1 not-first:border-t not-first:border-(--border-muted)">
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
