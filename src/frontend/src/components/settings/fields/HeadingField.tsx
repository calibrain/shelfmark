import { HeadingFieldConfig } from '../../../types/settings';

interface HeadingFieldProps {
  field: HeadingFieldConfig;
}

export const HeadingField = ({ field }: HeadingFieldProps) => (
  <div className="pb-1 not-first:mt-1 not-first:border-t not-first:border-(--border-muted) not-first:pt-5">
    <h3 className="mb-1 text-base font-semibold">{field.title}</h3>
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
              className="text-sky-600 underline dark:text-sky-400"
            >
              {field.linkText || field.linkUrl}
            </a>
          </>
        )}
      </p>
    )}
  </div>
);
