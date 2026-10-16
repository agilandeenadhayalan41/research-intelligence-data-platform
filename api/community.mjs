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
// visitor who signs in is shown by name. createCommunityHandler takes an
// optional resolveSessionUser(request) -> {login, name, avatarUrl, htmlUrl} |
// null, tried before the Bearer token: the sign-in code wires it to its
// session cookie here. Until then it is not passed (nobody has a session).

import {createCommunityHandler, createNodeHandler} from './_lib/community.mjs';

/** @type {((req: any, res: any) => Promise<void>) | null} */
let nodeHandler = null;

/**
 * @param {any} req
 * @param {any} res
 */
export default async function handler(req, res) {
  nodeHandler ??= createNodeHandler(createCommunityHandler({env: process.env}));
  await nodeHandler(req, res);
}
