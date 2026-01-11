interface FooterProps {
  buildVersion?: string;
  releaseVersion?: string;
  debug?: boolean;
}

export const Footer = ({ buildVersion, releaseVersion, debug }: FooterProps) => {
  // Determine version display - show "dev" if no version is set
  const versionDisplay = releaseVersion && releaseVersion !== 'N/A'
    ? releaseVersion
    : 'dev';

  // Truncate long build versions (e.g., git hashes) to 7 chars
  const truncatedBuild = buildVersion && buildVersion !== 'N/A'
    ? buildVersion.length > 7 ? buildVersion.slice(0, 7) : buildVersion
    : null;

  return (
    <footer
      className="mt-8 py-4"
      style={{
        paddingBottom: 'calc(1rem + env(safe-area-inset-bottom))',
      }}
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-baseline justify-center gap-2">
        <a
          href="https://github.com/calibrain/shelfmark"
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm font-medium opacity-70 hover:opacity-100 transition-opacity"
        >
          Shelfmark
        </a>
        <span
          className="text-xs opacity-40 font-normal"
          title={buildVersion && buildVersion !== 'N/A' ? `Build: ${buildVersion}` : undefined}
        >
          {versionDisplay}
          {truncatedBuild && ` (${truncatedBuild})`}
        </span>
        {debug && (
          <span className="text-xs px-1.5 py-0.5 rounded opacity-60" style={{ background: 'var(--border-muted)' }}>
            Debug
          </span>
        )}
      </div>
    </footer>
  );
};
