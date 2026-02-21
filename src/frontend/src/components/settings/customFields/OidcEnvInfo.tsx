import { CustomSettingsFieldRendererProps } from './types';

export const OidcEnvInfo = (_props: CustomSettingsFieldRendererProps) => {
  return (
    <div className="rounded-lg overflow-hidden border border-[var(--border-muted)]">
      <div
        className="px-3 py-1.5 text-xs font-medium opacity-60 border-b border-[var(--border-muted)]"
        style={{ background: 'var(--bg-soft)' }}
      >
        docker-compose.yml
      </div>
      <pre
        className="px-3 py-3 text-xs overflow-x-auto"
        style={{ background: 'var(--bg-soft)' }}
      >
        <code>
          <span className="opacity-60">environment:</span>{'\n'}
          {'  '}- <span className="text-blue-400">HIDE_LOCAL_AUTH</span>=<span className="text-green-400">true</span>
          {'    '}<span className="opacity-40"># Hide the local login form</span>{'\n'}
          {'  '}- <span className="text-blue-400">OIDC_AUTO_REDIRECT</span>=<span className="text-green-400">true</span>
          {'  '}<span className="opacity-40"># Skip login page, redirect straight to OIDC</span>
        </code>
      </pre>
    </div>
  );
};
