// GET  /api/auth/me                 → {signedIn, configured, user?}
// POST /api/auth/me?action=signout  → clears the session cookie (manual only)
//
// The browser asks who is signed in (the session cookie is HttpOnly). A
// session older than 30 days is re-issued, so it never expires for a visitor
// who comes back. Logic and tests: ../_lib/session.mjs,
// docs/site/test/session.test.mjs.

import {vercelHandler} from '../_lib/session.mjs';

export default vercelHandler('me');
