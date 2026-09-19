import { useCallback, useState } from 'react';

import { useMountEffect } from '../../../hooks/useMountEffect';
import type { ApiKeySummary, CreateApiKeyResponse } from '../../../services/api';
import {
  describeApiKeyStatus,
  EXPIRY_OPTIONS,
  formatApiKeyTimestamp,
  splitApiKeys,
} from './apiKeysPanelModel';

interface ApiKeysPanelProps {
  listKeys: () => Promise<ApiKeySummary[]>;
  revokeKey: (keyId: number) => Promise<unknown>;
  createKey?: (name: string, expiresInDays: number | null) => Promise<CreateApiKeyResponse>;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
}

const errorText = (error: unknown, fallback: string): string =>
  error instanceof Error && error.message ? error.message : fallback;

const buttonClass =
  'rounded-lg border border-(--border-muted) bg-(--bg-soft) px-3 py-1.5 text-sm font-medium transition-colors hover:bg-(--hover-surface) disabled:cursor-not-allowed disabled:opacity-50';

export const ApiKeysPanel = ({
  listKeys,
  revokeKey,
  createKey,
  onShowToast,
}: ApiKeysPanelProps) => {
  const [keys, setKeys] = useState<ApiKeySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [expiresInDays, setExpiresInDays] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [revealed, setRevealed] = useState<CreateApiKeyResponse | null>(null);
  const [copied, setCopied] = useState(false);
  const [pendingRevoke, setPendingRevoke] = useState<number | null>(null);
  const [revoking, setRevoking] = useState<number | null>(null);
  const [showRevoked, setShowRevoked] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setKeys(await listKeys());
    } catch (error) {
      setLoadError(errorText(error, 'Failed to load API keys'));
    } finally {
      setLoading(false);
    }
  }, [listKeys]);

  useMountEffect(() => {
    void refresh();
  });

  const handleCreate = async () => {
    if (!createKey) {
      return;
    }
    setCreating(true);
    try {
      const created = await createKey(name.trim(), expiresInDays);
      setRevealed(created);
      setCopied(false);
      setName('');
      setExpiresInDays(null);
      await refresh();
    } catch (error) {
      onShowToast?.(errorText(error, 'Failed to create API key'), 'error');
    } finally {
      setCreating(false);
    }
  };

  const handleCopy = async () => {
    if (!revealed) {
      return;
    }
    try {
      await navigator.clipboard.writeText(revealed.token);
      setCopied(true);
    } catch {
      onShowToast?.('Copy failed — select the key and copy it manually', 'error');
    }
  };

  const handleRevoke = async (keyId: number) => {
    setRevoking(keyId);
    try {
      await revokeKey(keyId);
      setPendingRevoke(null);
      if (revealed?.key.id === keyId) {
        setRevealed(null);
      }
      await refresh();
      onShowToast?.('API key revoked', 'success');
    } catch (error) {
      onShowToast?.(errorText(error, 'Failed to revoke API key'), 'error');
    } finally {
      setRevoking(null);
    }
  };

  const { active, revoked } = splitApiKeys(keys);
  const now = new Date();

  const renderKeyRow = (key: ApiKeySummary) => (
    <li
      key={key.id}
      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-(--border-muted) px-3 py-2 text-sm"
    >
      <div className="min-w-0">
        <div className="truncate font-medium">{key.name}</div>
        <div className="font-mono text-xs opacity-60">{key.key_prefix}…</div>
        <div className="text-xs opacity-60">
          {describeApiKeyStatus(key, now)} · created {formatApiKeyTimestamp(key.created_at)} · last
          used {formatApiKeyTimestamp(key.last_used_at)}
        </div>
      </div>
      {key.revoked_at === null &&
        (pendingRevoke === key.id ? (
          <div className="flex items-center gap-2">
            <span className="text-xs">Revoke this key?</span>
            <button
              type="button"
              className={buttonClass}
              disabled={revoking === key.id}
              onClick={() => {
                void handleRevoke(key.id);
              }}
            >
              {revoking === key.id ? 'Revoking…' : 'Confirm'}
            </button>
            <button
              type="button"
              className={buttonClass}
              disabled={revoking === key.id}
              onClick={() => setPendingRevoke(null)}
            >
              Cancel
            </button>
          </div>
        ) : (
          <button type="button" className={buttonClass} onClick={() => setPendingRevoke(key.id)}>
            Revoke
          </button>
        ))}
    </li>
  );

  return (
    <section className="space-y-3" aria-label="API keys">
      <div>
        <h4 className="text-sm font-medium">API keys</h4>
        <p className="text-xs opacity-60">
          Keys let scripts and integrations use Shelfmark as this account. Send one as{' '}
          <code className="font-mono">Authorization: Bearer &lt;key&gt;</code>.
        </p>
      </div>

      {revealed && (
        <div className="space-y-2 rounded-lg border border-(--border-muted) bg-(--bg-soft) p-3">
          <div className="text-sm font-medium">New key “{revealed.key.name}”</div>
          <p className="text-xs opacity-70">Copy it now. You will not be able to see it again.</p>
          <code
            className="block rounded bg-(--bg) p-2 font-mono text-xs break-all"
            data-testid="api-key-token"
          >
            {revealed.token}
          </code>
          <div className="flex gap-2">
            <button
              type="button"
              className={buttonClass}
              onClick={() => {
                void handleCopy();
              }}
            >
              {copied ? 'Copied' : 'Copy'}
            </button>
            <button type="button" className={buttonClass} onClick={() => setRevealed(null)}>
              Done
            </button>
          </div>
        </div>
      )}

      {createKey && (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void handleCreate();
          }}
        >
          <label className="flex flex-col gap-1 text-xs">
            Name
            <input
              type="text"
              value={name}
              maxLength={64}
              required
              placeholder="e.g. laptop script"
              onChange={(event) => setName(event.target.value)}
              className="rounded-lg border border-(--border-muted) bg-(--bg) px-3 py-1.5 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            Expiry
            <select
              value={expiresInDays === null ? '' : String(expiresInDays)}
              onChange={(event) =>
                setExpiresInDays(event.target.value === '' ? null : Number(event.target.value))
              }
              className="rounded-lg border border-(--border-muted) bg-(--bg) px-3 py-1.5 text-sm"
            >
              {EXPIRY_OPTIONS.map((option) => (
                <option key={option.label} value={option.value === null ? '' : option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className={buttonClass} disabled={creating || !name.trim()}>
            {creating ? 'Creating…' : 'Create key'}
          </button>
        </form>
      )}

      {(() => {
        if (loading) {
          return <div className="text-sm opacity-60">Loading API keys…</div>;
        }

        if (loadError) {
          return (
            <div className="flex items-center gap-2 text-sm">
              <span className="opacity-70">{loadError}</span>
              <button
                type="button"
                className={buttonClass}
                onClick={() => {
                  void refresh();
                }}
              >
                Retry
              </button>
            </div>
          );
        }

        if (active.length === 0) {
          return <div className="text-sm opacity-60">No active API keys.</div>;
        }

        return <ul className="space-y-2">{active.map(renderKeyRow)}</ul>;
      })()}

      {revoked.length > 0 && (
        <div>
          <button
            type="button"
            className="text-xs underline opacity-60"
            onClick={() => setShowRevoked((value) => !value)}
          >
            {showRevoked ? 'Hide' : 'Show'} revoked keys ({revoked.length})
          </button>
          {showRevoked && (
            <ul className="mt-2 space-y-2 opacity-60">{revoked.map(renderKeyRow)}</ul>
          )}
        </div>
      )}
    </section>
  );
};
