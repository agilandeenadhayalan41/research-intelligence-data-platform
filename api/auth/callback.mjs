// GET /api/auth/callback?code=…&state=… (GitHub redirects here)
//
// Checks the state against the cookie set by /api/auth/login, exchanges the
// code for a token, reads the public profile (GET https://api.github.com/user)
// and drops the token. Then sets the signed `rid_session` cookie (400 days)
// and redirects back to the page the visitor came from, with `#signed-in`,
// or with `#signin-error=<code>` (a fixed list of codes) when anything fails.
// Logic and tests: ../_lib/session.mjs, docs/site/test/session.test.mjs.

import {vercelHandler} from '../_lib/session.mjs';

export default vercelHandler('callback');
