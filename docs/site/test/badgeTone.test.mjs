import assert from 'node:assert/strict';
import {describe, test} from 'node:test';

import {STATUS_TONES, badgeTone, isBadgeTone, normalizeStatus} from '../src/lib/badgeTone.mjs';

describe('badgeTone', () => {
  test('register and readiness labels get their tone', () => {
    assert.equal(badgeTone('MET'), 'success');
    assert.equal(badgeTone('VERIFIED'), 'success');
    assert.equal(badgeTone('PARTIAL'), 'warning');
    assert.equal(badgeTone('CLOUD_UNVERIFIED'), 'warning');
    assert.equal(badgeTone('NOT_MET'), 'danger');
    assert.equal(badgeTone('NOT_READY'), 'danger');
    assert.equal(badgeTone('BLOCKED'), 'danger');
    assert.equal(badgeTone('CONTRACT_DEFINED'), 'info');
    assert.equal(badgeTone('DEMONSTRATED_LOCAL'), 'info');
    assert.equal(badgeTone('DEFERRED'), 'neutral');
  });

  test('case, spaces and hyphens do not matter', () => {
    assert.equal(normalizeStatus(' not ready '), 'NOT_READY');
    assert.equal(badgeTone('Not ready'), 'danger');
    assert.equal(badgeTone('ready-to-validate'), 'info');
    assert.equal(badgeTone('Accepted'), 'success');
  });

  test('unknown or empty labels are neutral', () => {
    assert.equal(badgeTone('SOMETHING_NEW'), 'neutral');
    assert.equal(badgeTone(''), 'neutral');
    assert.equal(badgeTone(undefined), 'neutral');
  });

  test('every status has exactly one tone', () => {
    const all = Object.values(STATUS_TONES).flat();
    assert.equal(new Set(all).size, all.length);
    for (const status of all) {
      assert.equal(status, normalizeStatus(status));
    }
  });

  test('isBadgeTone', () => {
    assert.ok(isBadgeTone('danger'));
    assert.ok(!isBadgeTone('red'));
    assert.ok(!isBadgeTone(undefined));
  });
});
