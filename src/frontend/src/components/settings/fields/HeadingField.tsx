import { HeadingFieldConfig } from '../../../types/settings';

interface HeadingFieldProps {
  field: HeadingFieldConfig;
}

export const HeadingField = ({ field }: HeadingFieldProps) => (
  <div className="pb-1 [&:not(:first-child)]:pt-5 [&:not(:first-child)]:mt-1 [&:not(:first-child)]:border-t [&:not(:first-child)]:border-black/10 [&:not(:first-child)]:dark:border-white/10">
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
              className="text-sky-500 hover:text-sky-400 underline"
            >
              {field.linkText || field.linkUrl}
            </a>
          </>
        )}
      </p>
    )}
  </div>
);
