// Vercel Node.js function behind /api/community: the docs site's shared likes
// and comments, stored as JSON files on the repository's community-data
// branch through the GitHub REST API.
//
// All the logic is in ./_lib/community.mjs (unit-tested from
// docs/site/test). Settings, read from the environment on the first request:
//
//   COMMUNITY_GITHUB_TOKEN  required, server only: a fine-grained token for
//                           this repository with Contents read and write
//   COMMUNITY_REPO          default agilandeenadhayalan41/research-intelligence-data-platform
//   COMMUNITY_DATA_BRANCH   default community-data (never main)
//   COMMUNITY_DATA_DIR      default community
//
// Without COMMUNITY_GITHUB_TOKEN every request answers 503 {configured: false}
// and the site keeps likes and comments in each visitor's browser instead.
//
// Likes and comments work for guests (the browser's random X-Guest-Id); a
// visitor who signs in with GitHub is shown by name. Who is writing, in this
// order:
//
// 1. the signed-in user of the `rid_session` cookie set by the optional
//    GitHub sign-in (api/auth/*): resolveSessionUser below verifies it with
//    getSessionUser from ./_lib/session.mjs (SESSION_SECRET, and
//    SESSION_SECRET_PREVIOUS after a rotation). No token is needed. Without
//    the sign-in settings it is always null. Writes carried by the cookie
//    must be same-site JSON requests (see sameSiteWrite in
//    ./_lib/community.mjs);
// 2. otherwise the owner of an `Authorization: Bearer <GitHub token>` header
//    (an optional alternative where sign-in is not available), checked with
//    GitHub and never stored;
// 3. otherwise a guest.

import {createCommunityHandler, createNodeHandler} from './_lib/community.mjs';
import {getSessionUser} from './_lib/session.mjs';

/** @type {((req: any, res: any) => Promise<void>) | null} */
let nodeHandler = null;

/**
 * @param {any} req
 * @param {any} res
 */
export default async function handler(req, res) {
  nodeHandler ??= createNodeHandler(
    createCommunityHandler({
      env: process.env,
      resolveSessionUser: (request) => getSessionUser(request, process.env),
    }),
  );
  await nodeHandler(req, res);
}
