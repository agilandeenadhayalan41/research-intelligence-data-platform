// GET /api/auth/login?returnTo=<same-site path>[&switch=1][&login=<github login>]
//
// Starts the optional GitHub sign-in: sets a short-lived signed state cookie
// and redirects to GitHub's own authorize page (no OAuth scopes: public
// profile only). `switch=1` asks GitHub to show its account picker; `login`
// suggests an account (from the site's account switcher). Answers 503 when
// GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET or SESSION_SECRET is
// missing. Logic and tests: ../_lib/session.mjs, docs/site/test/session.test.mjs.

import {vercelHandler} from '../_lib/session.mjs';

export default vercelHandler('login');
