// Colour tone of a status label, for the <Badge> MDX component. Pure function,
// no browser APIs, so it runs during server rendering and in node:test.
//
// The tone only colours the label; the label text itself is what the docs
// define (see docs/overview/glossary.md). A label not listed here is neutral.

/** @typedef {'success' | 'warning' | 'danger' | 'info' | 'neutral'} BadgeTone */

/** @type {readonly BadgeTone[]} */
export const BADGE_TONES = Object.freeze(['success', 'warning', 'danger', 'info', 'neutral']);

/** @type {Readonly<Record<BadgeTone, readonly string[]>>} */
export const STATUS_TONES = Object.freeze({
  // Proven or done.
  success: Object.freeze([
    'ACCEPTED',
    'COMPLETE',
    'MEASURED',
    'MET',
    'PASS',
    'READY',
    'SUCCESS',
    'VERIFIED',
  ]),
  // Partly proven, unverified in the cloud, or local/synthetic evidence only.
  warning: Object.freeze([
    'ASSUMED',
    'CLOUD_SYNTHETIC_MEASURED',
    'CLOUD_UNVERIFIED',
    'ESTIMATED',
    'FIXTURE_ONLY',
    'PARTIAL',
    'PERSONAL_DEV_QA_VERIFIED',
    'PROPOSED',
    'PROTOTYPE',
    'SEMANTIC_ONLY',
  ]),
  // Missing, failing or blocking.
  danger: Object.freeze([
    'BLOCKED',
    'CONFLICT',
    'ERROR',
    'FAILED',
    'MISSING',
    'NOT_IMPLEMENTED',
    'NOT_MET',
    'NOT_READY',
  ]),
  // Defined or demonstrated, not yet cloud evidence.
  info: Object.freeze([
    'CONTRACT_DEFINED',
    'DEFINED',
    'DEMONSTRATED_LOCAL',
    'LOCAL_POSTGRES_VERIFIED',
    'OBSERVED',
    'READY_TO_VALIDATE',
  ]),
  // Out of the current scope, or not decided.
  neutral: Object.freeze([
    'CONDITIONAL_DEFERRED',
    'DEFERRED',
    'DEPRECATED',
    'NOT_APPLICABLE',
    'OUT_OF_SCOPE',
    'SUPERSEDED',
    'UNKNOWN',
    'UNRESOLVED',
  ]),
});

const TONE_BY_STATUS = new Map(
  Object.entries(STATUS_TONES).flatMap(([tone, statuses]) =>
    statuses.map((status) => [status, /** @type {BadgeTone} */ (tone)]),
  ),
);

/**
 * "Not ready", "not-ready" and "NOT_READY" are the same status.
 *
 * @param {string} status
 * @returns {string}
 */
export function normalizeStatus(status) {
  return String(status ?? '')
    .trim()
    .replace(/[\s-]+/g, '_')
    .toUpperCase();
}

/**
 * @param {string | null | undefined} status
 * @returns {BadgeTone}
 */
export function badgeTone(status) {
  return TONE_BY_STATUS.get(normalizeStatus(status ?? '')) ?? 'neutral';
}

/**
 * True for a tone name, so a `tone` prop can override the status colour.
 *
 * @param {unknown} value
 * @returns {value is BadgeTone}
 */
export function isBadgeTone(value) {
  return typeof value === 'string' && BADGE_TONES.includes(/** @type {BadgeTone} */ (value));
}
