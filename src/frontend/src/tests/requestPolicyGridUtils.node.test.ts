import * as assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { TableFieldConfig } from '../types/settings.js';
import {
  getAllowedMatrixModes,
  getEffectiveCellMode,
  mergeRequestPolicyRuleLayers,
  normalizeExplicitRulesForPersistence,
  normalizeRequestPolicyDefaults,
  normalizeRequestPolicyRules,
  parseSourceCapabilitiesFromRulesField,
  RequestPolicyRuleRow,
} from '../components/settings/users/requestPolicyGridUtils.js';

const tableFieldFixture: TableFieldConfig = {
  type: 'TableField',
  key: 'REQUEST_POLICY_RULES',
  label: 'Request policy rules',
  value: [],
  columns: [
    {
      key: 'source',
      label: 'Source',
      type: 'select',
      options: [
        { value: 'direct_download', label: 'Direct Download' },
        { value: 'prowlarr', label: 'Prowlarr' },
        { value: 'irc', label: 'IRC' },
      ],
    },
    {
      key: 'content_type',
      label: 'Content type',
      type: 'select',
      options: [
        { value: 'ebook', label: 'Ebook', childOf: 'direct_download' },
        { value: 'ebook', label: 'Ebook', childOf: 'prowlarr' },
        { value: 'audiobook', label: 'Audiobook', childOf: 'prowlarr' },
        { value: 'ebook', label: 'Ebook', childOf: 'irc' },
      ],
    },
    {
      key: 'mode',
      label: 'Mode',
      type: 'select',
      options: [
        { value: 'download', label: 'Download' },
        { value: 'request_release', label: 'Request Release' },
        { value: 'blocked', label: 'Blocked' },
      ],
    },
  ],
};

describe('requestPolicyGridUtils', () => {
  it('parses dynamic source capabilities from rules field metadata', () => {
    const capabilities = parseSourceCapabilitiesFromRulesField(tableFieldFixture);

    assert.deepEqual(capabilities, [
      {
        source: 'direct_download',
        displayName: 'Direct Download',
        supportedContentTypes: ['ebook'],
      },
      {
        source: 'prowlarr',
        displayName: 'Prowlarr',
        supportedContentTypes: ['ebook', 'audiobook'],
      },
      {
        source: 'irc',
        displayName: 'IRC',
        supportedContentTypes: ['ebook'],
      },
    ]);
  });

  it('filters allowed matrix modes by default ceiling', () => {
    assert.deepEqual(getAllowedMatrixModes('download'), ['download', 'request_release', 'blocked']);
    assert.deepEqual(getAllowedMatrixModes('request_release'), ['request_release', 'blocked']);
    assert.deepEqual(getAllowedMatrixModes('request_book'), []);
    assert.deepEqual(getAllowedMatrixModes('blocked'), []);
  });

  it('keeps only meaningful persisted rules and removes inherited rows', () => {
    const sourceCapabilities = parseSourceCapabilitiesFromRulesField(tableFieldFixture);
    const defaultModes = normalizeRequestPolicyDefaults({
      ebook: 'request_release',
      audiobook: 'download',
    });

    const baseRules = normalizeRequestPolicyRules([
      { source: 'prowlarr', content_type: 'ebook', mode: 'blocked' },
    ]);

    const explicitRules = normalizeRequestPolicyRules([
      { source: 'direct_download', content_type: 'ebook', mode: 'request_release' }, // same as inherited default -> removed
      { source: 'prowlarr', content_type: 'ebook', mode: 'blocked' }, // same as inherited global rule -> removed
      { source: 'prowlarr', content_type: 'audiobook', mode: 'request_release' }, // meaningful override -> kept
      { source: 'irc', content_type: 'audiobook', mode: 'blocked' }, // unsupported pair -> removed
    ]);

    const persisted = normalizeExplicitRulesForPersistence({
      explicitRules,
      baseRules,
      defaultModes,
      sourceCapabilities,
    });

    assert.deepEqual(persisted, [
      { source: 'prowlarr', content_type: 'audiobook', mode: 'request_release' },
    ]);
  });

  it('overlays user rules on top of global rules for effective cell mode', () => {
    const globalRules: RequestPolicyRuleRow[] = [
      { source: 'prowlarr', content_type: 'ebook', mode: 'blocked' },
      { source: 'direct_download', content_type: 'ebook', mode: 'request_release' },
    ];
    const userRules: RequestPolicyRuleRow[] = [
      { source: 'direct_download', content_type: 'ebook', mode: 'blocked' },
    ];
    const mergedRules = mergeRequestPolicyRuleLayers(globalRules, userRules);
    const defaults = normalizeRequestPolicyDefaults({
      ebook: 'download',
      audiobook: 'download',
    });

    assert.equal(
      getEffectiveCellMode('direct_download', 'ebook', defaults, globalRules, userRules),
      'blocked'
    );
    assert.equal(
      getEffectiveCellMode('prowlarr', 'ebook', defaults, globalRules, userRules),
      'blocked'
    );

    assert.deepEqual(mergedRules, [
      { source: 'direct_download', content_type: 'ebook', mode: 'blocked' },
      { source: 'prowlarr', content_type: 'ebook', mode: 'blocked' },
    ]);
  });
});
