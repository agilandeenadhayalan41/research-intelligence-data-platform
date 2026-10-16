# Documentation site

This folder is a [Docusaurus 3](https://docusaurus.io/) site that publishes the
repository's `docs/` folder. It reads the Markdown **in place**: the pages are
the files under `docs/overview/`, `docs/architecture/` and the other folders of
`docs/`, so the same files render on GitHub and on the site. Nothing is copied
or moved.

On top of the docs the site has a home page, a blog with an in-browser editor,
and a few community features on every page: reading time, who last changed the
page, the people who changed it, likes and comments. The site is a **static
build plus a few small server functions** at the repository root, with no
database: `api/community.mjs` keeps the likes and comments that everyone
shares as JSON files in this repository (see
[Likes and comments](#likes-and-comments)), and `api/auth/` runs the optional
[GitHub sign-in](#github-sign-in-optional). Visitors are guests by default.
Everything else a visitor adds stays in their own browser (see
[Privacy](#privacy-what-is-stored-where)).

| Path | Purpose |
| --- | --- |
| `docusaurus.config.ts` | Site config: docs plugin pointed at `..` (the `docs/` folder), blog and pages plugins, the page-meta plugin, strict link checks, Mermaid, navbar and footer, `customFields` (`repo`, `communityStore`, `pageHistory`) |
| `sidebars.ts` | Sidebar: Overview, then the architecture docs grouped by area, with short labels |
| `src/pages/index.mdx` | The home page (`/`) |
| `src/components/Home/` | Home page building blocks: `Hero`, `FlowGraphic`, `FeatureCards`, `HomeSection`, `Icon` |
| `blog/` | Blog posts (`.md` or `.mdx`), served under `/blog` |
| `src/pages/blog/editor.tsx`, `src/components/BlogEditor/` | The blog editor (`/blog/editor`, `/blog/editor?post=<repo path>`) |
| `src/theme/BlogPostItems/`, `src/theme/BlogPostItem/Header/`, `src/components/BlogPostActions/` | The blog's **Write a post** toolbar above the post lists, and **Edit** and **Delete** next to each post's title |
| `src/components/GitHubTokenPrompt/` | The "Add a GitHub token in the account menu" prompt shared by the editor and the Delete dialog |
| `src/plugins/page-meta/index.js` | Build-time data for every doc and post: reading time, git history, and the raw source of blog posts for the editor |
| `src/theme/Root.tsx` | Wraps the site in the GitHub-user and community-store providers, and renders the sign-in toast |
| `src/theme/NavbarItem/ComponentTypes.tsx`, `src/components/GitHubUserNavbarItem/` | The `custom-githubUser` navbar item: "Sign in" and the account menu, or the avatar and username |
| `src/components/AuthNotice/` | The toast after signing in or out ("Signed in as @login", or why a sign-in did not complete) |
| `../../api/auth/login.mjs`, `callback.mjs`, `me.mjs` (repository root) | The sign-in functions on Vercel: start the GitHub OAuth flow, finish it, and say who is signed in |
| `../../api/_lib/session.mjs` (repository root) | Their logic: the signed session cookie, the OAuth state, `getSessionUser(req)` for other functions, and `createAuthHandlers()` for tests |
| `src/theme/DocItem/Content/`, `src/theme/DocItem/Footer/`, `src/theme/BlogPostItem/Footer/` | Wrappers around the theme components that add the page metadata, history and community blocks |
| `src/components/PageMetaBar/`, `PageHistory/`, `PageCommunity/` | Reading time and last update; contributors; likes and comments |
| `src/theme/MDXComponents/Img/`, `src/theme/Mermaid/` | Wrappers around the theme's Markdown image and Mermaid diagram that add the [zoom](#image-and-diagram-zoom) (the original `MDXComponents` maps `img` and `mermaid` to them, and `src/theme/MDXComponents.tsx` extends that map) |
| `src/components/Zoom/` | The zoom: lightbox, in-page toolbar, browser side of the downloads |
| `src/lib/panzoom.mjs`, `src/lib/usePanZoom.ts` | Pan and zoom math, and the React hook that drives both zoom modes with it |
| `src/lib/download.mjs` | Download file names, the standalone SVG of a diagram, the PNG export size |
| `src/types/react-dom.d.ts` | Types for `createPortal` (the site does not install `@types/react-dom`) |
| `src/lib/storage.mjs` | The community store (likes, comments, editor drafts) behind one adapter interface: the shared store (the site's API) and the browser store |
| `../../api/community.mjs`, `../../api/_lib/community.mjs` | At the repository root: the Vercel function behind `/api/community` (shared likes and comments, stored on the `community-data` branch) and its logic |
| `src/lib/github.mjs` | GitHub REST helpers: page commits (people only, `isAutomationAuthor`), public profiles, the blog pull-request flows (add or update a post, delete a post) |
| `src/lib/blogPosts.mjs` | Links between the blog and the editor: `blogEditorUrl(repoPath)` and the editor's `?post=` parsing |
| `src/lib/readingTime.mjs` | Word count and minutes to read for a Markdown source |
| `src/lib/auth.mjs` | Browser side of the sign-in: the `/api/auth/login` URL, the `#signed-in` / `#signin-error` fragment, `/api/auth/me`, the saved profile and account list, the guest id |
| `src/lib/GitHubUserContext.tsx`, `src/lib/CommunityStoreContext.tsx` | React hooks `useGitHubUser()` and `useCommunityStore()` |
| `test/` | Unit tests for `src/lib/*.mjs` and the functions in `../../api/` (`/api/community` with a fake GitHub API in `test/helpers/`, the sign-in functions, and both together over HTTP), the remark plugins and the MDX checker, plus an MDX compile of every doc (`node:test`, no network) |
| `src/remark/repo-links.mjs` | Rewrites relative links that leave `docs/` (for example `../../sql/...`) to GitHub URLs |
| `src/remark/jsx-links.mjs` | Resolves a component's link attribute (`<Card href="status.md">`) like a Markdown link |
| `src/theme/MDXComponents.tsx`, `src/components/Rich/` | The components every doc can use without an import: `Cards`/`Card`, `Steps`/`Step`, `Callout`, `Badge`, `Tabs`/`TabItem`, `Details`, `FileTree`, `Kbd` (see [Components](#components)) |
| `src/lib/badgeTone.mjs` | The colour of each status label in a `Badge` |
| `src/lib/htmlComments.mjs` | Turns HTML comments into MDX comments for the blog editor's preview (the build uses `@slorber/remark-comment`) |
| `scripts/check-mdx.mjs` | `npm run check:mdx`: compiles every doc and blog post as MDX and reports `file:line:column` |
| `src/css/custom.css` | Theme colours and design tokens, navbar, footer, table and diagram rules, avatar utility classes, focus rings, dark mode |
| `static/img/logo.svg` | Navbar logo and favicon |
| `static/img/blog/` | Images used by blog posts |

## Requirements

Node.js 20 or newer (tested with Node.js 24 and npm 11). Run every command from
this folder (`docs/site`).

## Local commands

```bash
npm ci            # install the exact versions from package-lock.json
npm start         # dev server with live reload on http://localhost:3000
npm run build     # production build into build/ (fails on any broken link)
npm run serve     # serve the production build on http://localhost:3000
npm run typecheck # type-check the TypeScript config files and components
npm run check:mdx # compile every doc and blog post as MDX, report file:line
npm test          # unit tests for src/lib and api/, including the MDX compile of every doc
```

`npm run serve -- --port 3077` serves on another port.

The build reads `git log` for the page history, so run it inside a git
checkout. Outside one (or when git is missing) it still succeeds; the
"last updated" and contributor fields are then empty until the browser loads
them from GitHub.

## Routes

| Route | Source |
| --- | --- |
| `/` | `src/pages/index.mdx`, the home page |
| `/overview/introduction` | `docs/overview/introduction.md`, the docs introduction (it used to be served at `/`) |
| `/<folder>/<file>` | Every other doc, by its path under `docs/` (for example `/architecture/roadmap`); `docs/adr/README.md` is `/adr/` |
| `/blog`, `/blog/YYYY/MM/DD/<slug>`, `/blog/tags/...` | The blog |
| `/blog/editor` | The blog editor; `/blog/editor?post=docs/site/blog/<file>` opens that post |
| `/api/community`, `/api/auth/login`, `/api/auth/callback`, `/api/auth/me` | Vercel functions in the repository-root `api/` folder, not part of the static build (so not served by `npm start` or `npm run serve`): shared likes and comments, and the optional GitHub sign-in |

## Home page

`src/pages/index.mdx` is an MDX page: the text is Markdown, the layout comes
from the components in `src/components/Home/`. It has a hero (title, the site
tagline from `docusaurus.config.ts`, the lead sentence, and the buttons
**Read the docs**, **Status** and **Blog**), a compact picture of the target
data flow, cards to the main sections (How it works, Status, Roadmap,
Architecture decisions, Lead coverage register, Getting started, Glossary),
the six properties of the governed path, the three engines, and a short
"Where things stand".

- **Its wording is reused from `docs/overview/introduction.md` and
  `docs/overview/how-it-works.md`.** The home page must make no claim those
  pages do not. When the status or the roadmap changes there, update
  "Where things stand" on the home page in the same pull request.
- Links are site routes (`/overview/status`), not file paths. The strict build
  fails on a route or `#anchor` that does not exist.
- The components are plain React with CSS modules, so they render on the
  server and need no browser APIs. A card links only when it has a `to`; its
  title is then the link, and the whole card is clickable.

```mdx
<FeatureCards>
  <FeatureCard title="Status" to="/overview/status" icon="status">
    What has been validated, where, and how strongly.
  </FeatureCard>
</FeatureCards>
```

Icons are inline SVG drawn in `Icon.tsx` (no image files, nothing fetched):
`flow`, `status`, `roadmap`, `decision`, `register`, `terminal`, `book`,
`database`, `laptop`, `server`.

## Page metadata, history and contributors

Every doc page and every blog post shows how many minutes it takes to read, when
it was last updated and by whom, the people who changed it (GitHub avatars and
logins, linked to their commits), likes and comments.

The data comes from two places:

1. **At build time** the local plugin `src/plugins/page-meta/index.js` walks
   `docs/**/*.md(x)` (except `docs/site/`) and `docs/site/blog/`. For each file
   it computes the reading time (`src/lib/readingTime.mjs`: 200 words a minute,
   front matter, HTML comments and link URLs left out, code counted at a third)
   and reads `git log --follow` for the last author, the last date and the
   commit authors. It publishes the result with `setGlobalData`, keyed by the
   repository path (`docs/architecture/roadmap.md`). This data is always
   there, so the reading time and the history are in the server-rendered HTML
   and need no network access. Git failures never fail the build: the git
   fields are then empty.
2. **In the browser** the page history asks the public GitHub API for the
   file's commits
   (`GET https://api.github.com/repos/{owner}/{repo}/commits?path=<repo path>&per_page=100`),
   caches the answer in `localStorage` for one hour per page, and shows the
   GitHub accounts behind the commits. On any error (rate limit, offline,
   blocked) it keeps showing the build-time data.

The build-time history is only as complete as the checkout: a shallow clone
(as CI and hosting providers often make; Vercel does unless
`VERCEL_DEEP_CLONE=true` is set in the project) holds only recent commits, and
its boundary commits look as if they added every file. The plugin ignores those
boundary commits, so a page whose last change is older than the clone has no
build-time "last updated" or contributors until the GitHub data loads in the
browser.

The repository the site talks to is set once, in `customFields.repo`
(`owner`, `name`, `branch`) in `docusaurus.config.ts`.

### People only: bots and AI tools are left out

The page history, the "Updated ... by ..." line, the contributor cards and the
commit counts credit **people only**. A commit is left out when its author is
automation, which `isAutomationAuthor(author, excludeList)` in
`src/lib/github.mjs` decides (the one filter, used for the GitHub data in the
browser and by the page-meta plugin at build time). Any one of these is enough:

- the GitHub account type is `Bot`;
- the GitHub login or the git author name ends with `[bot]`;
- the login or the name is in `customFields.pageHistory.excludeAuthors`,
  compared ignoring case, a trailing `[bot]`, and spaces, hyphens,
  underscores and dots (so the git name `Cursor Agent` matches `cursoragent`
  and `Claude Code` matches `claude-code`);
- the author email ends with `@anthropic.com` (so `noreply@anthropic.com`
  too) or is a `<name>[bot]@users.noreply.github.com` address. Emails are read
  only for this check: they are never published in the build data, cached or
  shown.

```ts
// docusaurus.config.ts
customFields: {
  pageHistory: {
    // The default list (DEFAULT_EXCLUDED_AUTHORS in src/lib/github.mjs):
    // claude, claude-code, anthropic-claude, copilot, github-copilot,
    // copilot-swe-agent, github-actions, dependabot, renovate, vercel, cursor,
    // cursoragent, devin-ai-integration, codex, chatgpt-codex-connector,
    // gemini-code-assist, sweep-ai, coderabbitai.
    excludeAuthors: [...DEFAULT_EXCLUDED_AUTHORS, 'another-bot'],
  },
},
```

The list replaces the default one; remove the key to use the default list, or
set `[]` to keep only the bot, `[bot]` and email rules. Changing it takes a
rebuild for the build-time data; the browser's one-hour cache of GitHub data
is keyed by the list, so it is asked again.

- "Last updated ... by ..." is the most recent commit **by a person** (its
  date and author), even when automation changed the page later.
- "N people have changed this page in M commits" counts people and their
  commits only. When automation also changed the page, the history's footer
  says "Changes by bots and AI tools are not listed."
- A page changed **only** by automation shows the date of its last change
  without a name ("Updated Oct 16, 2026" in the bar above the page, "Last
  updated Oct 16, 2026" in the history), no contributor cards, and the note
  "Changes so far were made by automation."
- In the build-time data, excluded commits only appear as a count and the date
  and commit of the latest one (`automation: {commits, lastDate, lastSha}`),
  never as a name or email. In a shallow clone, a page whose only commits after
  the boundary are automation's has an unknown build-time history (people may
  have changed it before the boundary) until the GitHub data loads.

## GitHub sign-in (optional)

Visitors are **guests by default**. Opening any link lands on that page (the
home page for `/`): there is no login page, and nothing asks anyone to sign in
on arrival. Reading, likes and comments all work as a guest; comments are then
signed "Guest". A visitor who wants their likes and comments to show "I am this
GitHub user" can sign in, with GitHub checking who they are.

### How it works

1. **Sign in** (navbar, right; or **Sign in with GitHub** under the comment
   form) is a plain link to `/api/auth/login?returnTo=<this page>`. The
   function makes a random `state` (32 bytes), keeps it with the return path
   in a signed, HttpOnly cookie (`rid_oauth_state`, 10 minutes, path
   `/api/auth`) and redirects to **GitHub's own sign-in and authorize page**.
   It asks for **no OAuth scope**: the app can read the public profile only.
2. GitHub checks who the person is and sends the browser back to
   `/api/auth/callback?code=…&state=…`. The function compares the state with
   the cookie (in constant time), exchanges the code for a token
   (`POST https://github.com/login/oauth/access_token`), reads the profile
   (`GET https://api.github.com/user`: login, id, name, avatar, profile URL)
   and **drops the token**. It is never stored, logged or sent to the browser.
3. It sets the session cookie `rid_session` and redirects back to the page
   with `#signed-in`. A failure comes back as `#signin-error=<code>`, with a
   code from a fixed list (`access_denied`, `github_error`, `bad_state`,
   `missing_code`, `exchange_failed`, `profile_failed`, `server_error`). The
   site shows a short message and removes the fragment from the address bar.
   No code, token or secret is ever put in a URL.
4. On every page load the site asks `GET /api/auth/me` who is signed in (the
   cookie is HttpOnly, so scripts cannot read it) and shows the avatar and
   username in the navbar.

### Staying signed in, switching, signing out

- The session cookie lasts **400 days** (the longest a browser keeps a
  cookie), and `/api/auth/me` re-issues it once it is older than 30 days, so a
  visitor who comes back stays signed in. **Nothing signs anyone out
  automatically**, and the last known profile is kept in `localStorage` so the
  navbar shows it at once.
- **Signing in as a different user replaces the active account.** Accounts
  used before stay in the account menu (**Other accounts used in this
  browser**, or **Accounts used in this browser** when signed out), and nothing
  they posted is deleted. **Switch account** goes back to GitHub with its
  account picker (`prompt=select_account`); picking an account from the list
  suggests it to GitHub (`login=<name>`). GitHub decides which account signs
  in.
- **Sign out** in the account menu is the only way out. It clears the session
  cookie (`POST /api/auth/me?action=signout`, refused from other sites) and
  then the active profile, and keeps the account list. The cookie goes first
  so that the page's likes and comments, read again for the new identity, are
  a guest's. If the site cannot be reached, the visitor stays signed in and
  the menu says so.

### What is stored

| Where | What |
| --- | --- |
| Cookie `rid_session` (HttpOnly, Secure, SameSite=Lax, path `/`, 400 days) | base64url JSON `{v: 1, login, id, name, avatarUrl, htmlUrl, iat}`, then `.` and an HMAC-SHA256 signature made with `SESSION_SECRET`. Public profile data only; signed, not encrypted. |
| Cookie `rid_oauth_state` (HttpOnly, Secure, path `/api/auth`, 10 minutes) | The random state and the return path, signed. The callback clears it. |
| `localStorage` `rid-github:v1:user` | The active profile (login, name, avatar URL, profile URL), so the navbar shows it at once. |
| `localStorage` `rid-github:v1:accounts` | The account switcher: login, name and avatar URL of up to 8 accounts used in this browser. |
| The server | Nothing. There is no user database, and the GitHub token is dropped in the callback. |

The functions never log codes, tokens, cookies or secrets, and send no CORS
headers (same origin only).

### Setting it up (Vercel)

1. On GitHub, open **Settings > Developer settings > OAuth Apps > New OAuth
   App** (for the account or organization that should own it). Homepage URL:
   `https://<production domain>`. **Authorization callback URL:
   `https://<production domain>/api/auth/callback`**. Then generate a client
   secret.
2. In the Vercel project, **Settings > Environment Variables**, add for the
   **Production** environment:
   - `GITHUB_OAUTH_CLIENT_ID`: the OAuth App's client ID;
   - `GITHUB_OAUTH_CLIENT_SECRET`: its client secret;
   - `SESSION_SECRET`: at least 32 random characters (for example the output
     of `openssl rand -base64 48`). Changing it signs everyone out, unless the
     old value stays in `SESSION_SECRET_PREVIOUS` for a while: it is still
     accepted, and sessions are re-signed with the new secret on the next
     visit.
3. Redeploy. Vercel deploys the repository-root `api/` folder as functions
   with the site; they have no dependencies (`api/_lib/` is not a route).

When a variable is missing (or `SESSION_SECRET` is shorter than 32
characters), `/api/auth/me` answers `{signedIn: false, configured: false}`,
the site hides sign-in (the account menu offers the typed-username fallback
instead, see [Local development](#local-development)), `/api/auth/login`
answers 503 "Sign-in is not configured on this deployment", and everything
else keeps working as guest.

**Preview deployments** have their own domains, and GitHub only sends people
back to the callback URL registered in the OAuth App, so sign-in works only on
the domain registered there. Keep the variables scoped to Production: previews
then show sign-in as unavailable. To try sign-in on previews, register a
second OAuth App for the preview domain and set its variables for the Preview
environment only.

### Local development

`npm start` and `npm run serve` serve the static site only. The functions do
not run there, so `/api/auth/me` does not answer and the **Sign in** link ends
on a 404. The site then shows any saved profile as **not verified on this
deployment**, and the account menu offers the old typed-username form as the
fallback (**Use a GitHub username instead**, labelled *not verified*). The
same form is offered on a deployment where sign-in is not configured. It reads
the public profile of the username typed and only labels likes and comments
kept in this browser; nobody checks that the account is yours. On shared
likes and comments it shows your name only together with a GitHub token,
which the site checks. To run the real sign-in locally, use `vercel dev` with
a separate OAuth App whose callback is
`http://localhost:3000/api/auth/callback`.

### The optional GitHub token

The account menu (the arrow next to **Sign in**, or the avatar when signed in;
**GitHub token** where sign-in is not available) also has an optional
**GitHub token** field. It is separate from signing in: the sign-in session
never gives the site a token, and a visitor signed in with GitHub never needs
one for likes and comments. The token is used for:

- the blog editor's **Open pull request** and the blog's **Delete** (which
  opens a pull request too); without a token both show the same prompt, "Add
  a GitHub token in the account menu";
- higher GitHub API rate limits for the page history;
- where GitHub sign-in is not available (a deployment without the OAuth
  settings, or no sign-in functions), showing your name on shared likes and
  comments: with the typed username **and** a token, the site's
  `/api/community` checks the token with GitHub (see
  [Likes and comments](#likes-and-comments)). Without one you like and
  comment as a guest.

Where it goes and how to choose one:

- It is kept in `localStorage` **on this device only**. It is sent to
  `https://api.github.com`, as the `Authorization` header. Only in the last
  case above (no GitHub sign-in on the deployment, a typed username and a
  token) is it also sent with each like, unlike, comment or deletion to this
  site's own `/api/community`, **only to confirm your login**: the function
  calls `GET https://api.github.com/user` with it and keeps nothing but the
  answer's login, name and avatar (in memory, for 10 minutes, under the
  token's SHA-256). It is never stored, logged or sent anywhere else, and
  error messages never include it. Signed in with GitHub, or as a guest, a
  saved token is never sent with likes and comments.
- **To show your name on likes and comments, any token works**, even a
  fine-grained token with only the default public, read-only access and no
  permissions: the site only needs to know whose it is.
- Anything in `localStorage` can be read by scripts running on the site's
  origin, and stays until it is removed. Give the token an expiry date and the
  least access that works. With push access to this repository, a
  **fine-grained personal access token** limited to it, with only
  *Contents: read and write* and *Pull requests: read and write*, is enough.
  Without push access the editor forks the repository, and a fine-grained
  token cannot be granted access to another owner's repository, so a classic
  token with only the `public_repo` scope is the simple choice there. The
  same token works for deleting a post. Remove
  the token from the menu on a shared computer, and revoke it on GitHub when it
  is no longer needed.

### GitHub API rate limits

GitHub allows **60 requests an hour per IP address without a token** and
**5,000 an hour with a token** for the REST API. From the browser, the site
makes at most one request per page and hour for the page history (the cache
absorbs the rest), and one per username in the typed-username fallback;
GitHub sign-in makes none (the callback function reads the profile). When the
limit is reached, pages fall back to the build-time history; adding a token
raises the limit.

## Likes and comments

Each doc page and blog post has a like button with a count, and comments with
the author's avatar, name and date. Authors can delete their own comments. A
comment is 1 to 5,000 characters. Nothing about signing in ever blocks reading
or guest writes.

Likes and comments are **shared with everyone and stored permanently**, as
JSON files in this GitHub repository. Every like, unlike, comment and deleted
comment is a commit. The page says so next to the buttons ("Shared with
everyone. Saved in this site's GitHub repository."). Blog editor drafts are
not shared: they stay in the visitor's browser.

**Guests come first.** Anyone can read, like and comment right away, without
signing in. Under the comment box the page says "Commenting as a guest ·
Sign in with GitHub to show your name" (the link goes to GitHub's sign-in and
back to the page; where sign-in is not available it just says "Commenting as a
guest"). Who a like or comment is from follows who the visitor is at that
moment:

- **As a guest**, the browser makes a random id the first time it likes or
  comments (a UUID v4, kept in `localStorage` under
  `rid-community:v1:guest-id`) and sends it as the `X-Guest-Id` header with
  its writes and, from then on, its page reads. A visitor who only reads never
  gets one. The
  site keeps only a hash of it, so the guest can later unlike their like and
  delete their own comments (shown as "Guest (you)"), and nobody else can.
  Guest comments show as **Guest**, with no avatar. A guest who clears site
  data, or uses another browser or a private window, is a new guest and can no
  longer delete their earlier comments (a maintainer can, see
  [Moderation](#moderation)).
- **Signed in with GitHub** (the navbar's **Sign in**, see
  [GitHub sign-in](#github-sign-in-optional)), likes and comments carry the
  verified GitHub login, name and avatar from the signed session cookie, which
  the browser sends with every request to the site. **No token is needed.**
  The comment hint says "Commenting as @login", the like button shows whether
  this account likes the page, and **Delete** appears on this account's
  comments. After **Sign out** (or signing in as someone else) the page reads
  the likes and comments again for the new identity.
- **With a token instead**, where GitHub sign-in is not available: a typed
  username (labelled *not verified*) **and** a GitHub token in the account
  menu. The token is what proves who the visitor is (the typed username alone
  proves nothing); the hint says "Commenting as @login, confirmed with your
  GitHub token". A typed username without a token is still a guest ("Add a
  GitHub token to comment as @login").
- The guest id is sent with every write, so comments written as a guest in
  this browser stay deletable after signing in.
- Names never come from the request: the function ignores any name, login or
  avatar in the body of a guest write, and refuses (403) a write whose `login`
  is not the verified user.

### How shared storage works

```text
browser ──GET  /api/community?page=/docs/x.md   X-Guest-Id: <uuid>  [Cookie: rid_session]──▶ api/community.mjs ──contents API──▶ community-data branch
        ──POST /api/community  X-Guest-Id: <uuid>
                               [Cookie: rid_session]                     (signed in with GitHub)
                               [Authorization: Bearer <visitor token>]   (typed username + token, no sign-in)
                               {action, page, body?, id?, login?}
                                  │ who is it? 1. the session cookie: getSessionUser(request)  2. the Bearer token,
                                  │ checked with GET https://api.github.com/user  3. otherwise a guest: sha256(guest id)
                                  └ write with the server token (COMMUNITY_GITHUB_TOKEN): one commit
```

- **The function.** `api/community.mjs` at the repository root is a Vercel
  Node.js function (no dependencies). It is a thin wrapper around
  `api/_lib/community.mjs` (the underscore keeps that file from becoming a
  route), which holds all the logic and is unit-tested from `docs/site/test`.
- **Reads.** `GET /api/community?page=<pageId>` answers
  `{configured: true, pageId, likes: {count, likedBy, liked}, comments: [...]}`
  (comments oldest first, each
  `{id, pageId, author: {login, name, avatarUrl}, body, createdAt, canDelete}`).
  `count` includes guests; `likedBy` lists only the GitHub logins (guests are
  counted, never listed). `liked` and `canDelete` are worked out for **this
  request's caller** (their session, else their `X-Guest-Id`): that is the
  only way a guest learns which like and comments are theirs, and no guest's
  key ever appears in an answer. A missing or malformed guest id on a read
  just means `false`. A page without a file is empty. Each function instance
  caches a page for 20 seconds; responses are `Cache-Control: no-store`.
  `GET /api/community` without `page` is a status probe (`{configured: true}`,
  no GitHub call).
- **Writes.** `POST /api/community` with a JSON body
  `{action: 'like' | 'unlike' | 'comment' | 'delete', page, body?, id?, login?}`.
  The writer is, in this order:
  1. the **signed-in user**: `api/community.mjs` passes
     `resolveSessionUser: (req) => getSessionUser(req, process.env)` (from
     `api/_lib/session.mjs`) to `createCommunityHandler`, so a valid
     `rid_session` cookie names the writer, with the login, name and avatar
     from the session. No token and no GitHub call are needed. Without the
     sign-in settings (`SESSION_SECRET` and the OAuth variables) it is always
     null, and a cookie that does not verify is ignored (the request is then a
     guest's or a token's);
  2. otherwise, when there is an `Authorization` header, the owner of the
     Bearer token: the function calls `GET https://api.github.com/user` with it
     to learn the login (cached for 10 minutes under the token's SHA-256, never
     the token itself). A header that is not a token GitHub accepts is 401: it
     is never quietly turned into a guest write;
  3. otherwise a **guest**, from `X-Guest-Id` (required then; it must be a
     UUID v4, else 400). The function keeps only
     `guestKey` = the first 16 hex characters of the SHA-256 of the id (lower
     case), never the id.

  The write is made with the function's **own** token. `like` and `unlike` are
  idempotent and only touch the caller's own like; a comment is stored with
  the name and avatar from the session or GitHub, or as `Guest`; `delete`
  works only on your own comment: a user's by its login, a guest's by the same
  guest id (also sent along by a signed-in browser), 403 otherwise, 404 when it
  is gone. When the browser sends `login` (the signed-in username), it must be
  the verified user, otherwise 403 "this token belongs to @…"; a guest's
  `login`, `name` or `avatarUrl` in the body is ignored. The answer is the
  page's new state, seen by the caller.
- **Errors.** 400 for a bad page id (`^/[A-Za-z0-9/_.~%-]{0,300}$`), an unknown
  action, a comment that is not 1-5,000 characters after trimming, or a guest
  write without a valid `X-Guest-Id`; 401 for a token GitHub does not accept;
  403 for someone else's comment or a `login` that is not yours; 405 for
  methods other than GET and POST; 409 when a page is full (1,000 comments, or
  a page file over 900,000 bytes); 413 for a body over 16 KB; 429 when rate
  limited (with `Retry-After`); 502 when GitHub cannot be reached; 503
  `{configured: false}` without the server token, and 503 "busy, try again"
  after repeated write conflicts. No message ever contains a token or a guest
  id. The function sends no CORS headers: only the site itself can call it
  from a browser.
- **The page id.** The site's page id is the page's repository path
  (`docs/overview/introduction.md`, `docs/site/blog/<file>.md`); the API uses
  it with a leading slash (`/docs/overview/introduction.md`), other
  characters percent-encoded (`toApiPageId` in `src/lib/storage.mjs`).

### The data branch

Commits go to a dedicated branch, **`community-data`**, never to `main`, so the
history, CI and deployments of `main` stay clean. If the branch does not exist,
the first write creates it as an **orphan branch** (no shared history with
`main`) holding `community/README.md`, which explains the folder, and its own
root `vercel.json` with `{"git": {"deploymentEnabled": false}}`. Vercel reads
`vercel.json` from the pushed commit, not from `main`, so this copy is what
keeps the branch's commits from starting deployments (any data branch the
function creates gets it, including a separate Preview branch). The
`community-data` entry under `git.deploymentEnabled` in `main`'s `vercel.json`
stays as a second guard. The branch has no workflow files, so CI never runs on
it (`.github/workflows/tests.yml` also lists it under `branches-ignore`).

One file per page, `community/pages/<id>.json`, where `<id>` is the first 32
hex characters of the SHA-256 of the API page id:

```json
{
  "version": 1,
  "pageId": "/docs/overview/introduction.md",
  "likes": [
    {"login": "octocat", "at": "2026-10-16T12:00:00.000Z"},
    {"login": null, "guestKey": "3f9c0a7d51e2b864", "at": "2026-10-16T12:05:00.000Z"}
  ],
  "comments": [
    {
      "id": "6f1c…",
      "login": "octocat",
      "name": "The Octocat",
      "avatarUrl": "https://avatars.githubusercontent.com/u/583231?v=4",
      "body": "Clear and useful, thanks.",
      "createdAt": "2026-10-16T12:01:00.000Z"
    },
    {
      "id": "9a2e…",
      "login": null,
      "name": "Guest",
      "guestKey": "3f9c0a7d51e2b864",
      "avatarUrl": null,
      "body": "Same question as above.",
      "createdAt": "2026-10-16T12:06:00.000Z"
    }
  ]
}
```

A guest entry has `login: null` and a `guestKey` (16 hex characters of the
SHA-256 of the browser's random guest id; the id itself is never written).
Likes are unique by login or guest key; comments are oldest first. The format
only gained the guest fields, so files written before guests existed read
unchanged. Every update goes
through the contents API with the file's `sha` (compare and swap): when
someone else changed the file first (409 or 422), the function reads it again
and retries up to 4 times with a small random pause, then answers 503
"busy, try again". The commit message is `community: <action> on <pageId>`,
made as the owner of the server token. A page takes at most 1,000 comments,
and a new comment is refused (409 "this page has reached its size limit") when
it would grow the page file past 900,000 bytes, which keeps every answer far
below Vercel's 4.5 MB response limit and the file under the contents API's
1 MB inline limit. Likes, unlikes and deletes are never refused for size.

To find the file of a page:

```bash
printf '%s' '/docs/overview/introduction.md' | sha256sum | cut -c1-32
```

### Setup for maintainers

1. Create a **fine-grained personal access token** on GitHub (Settings,
   Developer settings, Fine-grained tokens) with **Repository access: only
   this repository** and **Repository permissions: Contents, read and write**
   (Metadata read-only is added automatically). Nothing else. Give it an
   expiry date.
2. In the Vercel project, **Settings > Environment Variables**, add
   `COMMUNITY_GITHUB_TOKEN` with that token for **Production** and
   **Preview**. Optional: `COMMUNITY_REPO` (default
   `agilandeenadhayalan41/research-intelligence-data-platform`),
   `COMMUNITY_DATA_BRANCH` (default `community-data`; `main` and `master` are
   refused) and `COMMUNITY_DATA_DIR` (default `community`).
3. **Redeploy** (environment variables apply to new deployments only).
4. **Never commit the token**, and never put it in `docusaurus.config.ts` or
   any file the browser receives. If it is ever exposed, revoke it on GitHub
   at once, create a new one and update the Vercel variable. Rotate it before
   it expires.

Preview deployments write to the same branch as production when they share
the variables, so testing a pull request's preview adds real likes and
comments; set `COMMUNITY_DATA_BRANCH` to another branch for Preview to keep
them apart.

### Without the server token (and with `npm start`)

The browser asks `GET /api/community` once per visit. Unless it answers
`configured: true`, the site keeps likes and comments **in this browser only**,
as before (IndexedDB, else `localStorage`, else memory), and says so: "Saved in
this browser only. Other visitors do not see your likes and comments." That is
what happens with `npm start` and `npm run serve` (no function runs there), on
a deployment without `COMMUNITY_GITHUB_TOKEN`, and when the API cannot be
reached. Nothing is moved to the repository later.

To try the function locally, run `vercel dev` from the repository root with the
variables in a local `.env` file that is never committed, or read
`docs/site/test/communityServer.test.mjs`, which runs it on a local HTTP server
against a fake GitHub.

### Rate limits and spam

In each function instance's memory (best effort), for guests and users alike:

- **20 writes per 10 minutes per client IP**, every write counted (failed and
  refused ones too), whoever makes it;
- on top of that, **10 guest writes per 10 minutes per client IP and guest
  id**;
- **120 reads per minute per client IP**.

Beyond that the function answers 429 with `Retry-After`. GitHub's own limits
apply to the server token (5,000 requests an hour, and fewer for
content-creating requests); when GitHub rate limits the function it answers
503.

Guest writes make the site easy to use and also easy to spam: a guest id is
free, so the per-IP limit is what really bounds one network, and the limits
live in each instance's memory, so a burst spread over many instances or IPs
gets further. Every write is still one commit on the data branch, with its
time, which makes spam easy to see and to undo (see Moderation). A comment is
plain text (1-5,000 characters, never rendered as HTML or Markdown), so links
in it are not clickable. A page refuses new comments once it has 1,000 or its
file reaches 900,000 bytes. If a spam wave gets through, the quickest stop is
to remove `COMMUNITY_GITHUB_TOKEN` from the deployment and redeploy: the API
then answers `configured: false` and every browser falls back to keeping its
own likes and comments, while the shared data stays readable on the branch.

### Moderation

The data is plain JSON on the `community-data` branch. To remove a comment or a
like, edit the page's file on that branch (on GitHub, or in a checkout of the
branch) and commit; to clear a page, delete its file. Keep the JSON valid: a
file that is not valid JSON reads as an empty page, and the function refuses to
write to it (500) until it is fixed, so nothing is lost. The branch history
keeps every change, including removed comments; rewriting that history is the
only way to erase a comment completely.

Guest entries carry a `guestKey`: everything one browser wrote as a guest has
the same key, so a spammer's comments are easy to find and remove together
(search the branch for the key). The key cannot be turned back into the guest
id, so it gives nobody the power to delete or unlike as that guest. A comment
removed by hand disappears from the site within 20 seconds (the function's
read cache).

### Privacy

Shared likes and comments are public: the login, display name and avatar URL
from GitHub (or, for a guest, only "Guest" and the guest key), the comment
text and the dates are committed to a public repository and stay in its
history after a deletion. The guest id stays in the browser and goes only to
this site's `/api/community`, which keeps only its hash and never returns it;
the guest key is never shown on the site, but it is in the public branch, so
someone reading the branch can tell which guest comments came from the same
browser. The session cookie only says who is signed in (it holds the public
profile, signed). The visitor's token, when one is sent, is used only to ask
GitHub who they are and is never stored. Drafts never leave the browser.

### Sign-in and the community API

The sign-in and the shared likes and comments meet in `api/community.mjs`:
`createCommunityHandler({resolveSessionUser})` takes
`(request) => {login, name, avatarUrl, htmlUrl} | null` (sync or async; the
request carries `method`, `query`, `headers`, `body` and `ip`) and tries it
before the Bearer token, on reads too (for `liked` and `canDelete`), and the
function wires it to `getSessionUser(request, process.env)`, which verifies
the `rid_session` cookie (with `SESSION_SECRET`, or `SESSION_SECRET_PREVIOUS`
after a rotation). What it returns is checked (a valid GitHub login, an https
avatar, else ignored); the author of a write always comes from it or from
GitHub, never from the request body. Guests keep working as they are: author
`{login: null, name: 'Guest'}`, likes keyed by `guest:<guestKey>` from
`X-Guest-Id`, deletion only with the same guest id, and the rate limits apply
to both. A resolver that throws fails a write with 500 (it is never turned
into a guest write) and leaves reads working.

A cookie goes with any request to the site, including a form or `text/plain`
POST from another site, so a write by a session user is refused (403) unless
a cross-site page could not have made it: its `Content-Type` must be
`application/json` (which needs a CORS preflight, and the function grants
none), `Sec-Fetch-Site` must not be `cross-site`, and an `Origin` header, when
sent, must be this host (`X-Forwarded-Host`, else `Host`). The site's own
writes meet all three. Guest and token writes need `X-Guest-Id` or
`Authorization`, custom headers that already force a preflight. Set the
session cookie is set with `SameSite=Lax` (and `HttpOnly`, `Secure`,
`Path=/`) as a second layer. `test/communitySession.test.mjs` runs the real
`api/community.mjs` and `api/auth/*` functions on a local HTTP server against
a fake GitHub: a GitHub sign-in, then a like and a comment carried by the
session cookie alone (the verified author), a guest write, and a cross-site
cookie write refused. See also
[Privacy: what is stored where](#privacy-what-is-stored-where).

### The storage adapter

All three kinds of data go through one interface, `CommunityStore`, in
`src/lib/storage.mjs`. Components never touch the API, IndexedDB or
`localStorage` directly; they call `useCommunityStore()` and the methods below
(all async).

| Method | Returns |
| --- | --- |
| `getLikes(pageId)` | `{count, likedBy}` |
| `toggleLike(pageId, userKey)` | `{count, likedBy, liked?}`; `userKey` is the GitHub login, or `'guest'`. `liked` is set by the shared store for a guest (guests are not in `likedBy`) |
| `listComments(pageId)` | comments, oldest first: `{id, pageId, author: {login, name, avatarUrl}, body, createdAt, canDelete?}` (`canDelete` from the shared API) |
| `addComment(pageId, {author, body})` | the new comment; throws when the trimmed body is not 1-5000 characters |
| `deleteComment(pageId, id, userKey)` | `true` when the comment was the user's and was deleted |
| `getDraft(key)`, `saveDraft(key, {content, format})` | a draft `{key, content, format, updatedAt}` (or `null`) |
| `listDrafts()`, `deleteDraft(key)` | drafts, newest first; `true` when deleted |
| `backend()` | where likes and comments are kept: `'shared'`, or `'indexedDB'`, `'localStorage'` or `'memory'` (the note next to the like button) |
| `draftsBackend()` | where drafts are kept: always the browser (`'indexedDB'`, `'localStorage'` or `'memory'`) |

`createCommunityStore({kind})` picks the implementation; `kind` comes from
`customFields.communityStore` in `docusaurus.config.ts`:

- **`'shared'`** (the site's setting): `createSharedStore({baseUrl = '/api/community', getToken, getSessionLogin, getGuestId, fetchImpl, fallback})`.
  Likes and comments go to the API; `toggleLike` sends `like` or `unlike`
  from the last state it saw **for the current identity**. Every write sends
  `X-Guest-Id`, and so do page reads once the browser has one
  (`getGuestId({create})`; `createCommunityStore` keeps it in `localStorage`
  under `rid-community:v1:guest-id` through `createGuestIdSource`, the one
  guest-id implementation, or in memory when storage is blocked; a read never
  creates one). Requests are same-origin, so the browser adds the session
  cookie by itself. Who is writing is read at each call:
  `getSessionLogin()` is the login that `/api/auth/me` confirmed (signed in
  with GitHub: no token is sent, and `login` is sent so the API refuses a
  write if the cookie now belongs to someone else); otherwise `getToken()`
  is the account menu's token, given only for a typed username where sign-in
  is not available (then `Authorization: Bearer …` and `login` are sent);
  otherwise the write is a guest write and sends no name. The store keeps the
  last page state per identity (`guest`, `session:<login>` or `token`), so
  after a sign-out or an account change it never reuses another identity's
  `liked` flag. Refusals reject
  with `CommunityApiError` (with the HTTP `status`). Drafts always go to
  `fallback`, the browser store. When the API does not answer
  `configured: true`, everything goes to `fallback` (and no guest id is made).
  `baseUrl` must be a path on this site and every request is sent with
  `redirect: 'error'`, so neither the token nor the guest id is ever sent to
  another origin.
- **`'browser'`**: everything in this browser: **IndexedDB** (database
  `rid-community`, version 1, object stores `likes`, `comments` and `drafts`),
  otherwise **`localStorage`** (keys prefixed `rid-community:v1:`), otherwise
  an **in-memory** store (private windows that block storage, server-side
  rendering), lost on reload.

Any other kind throws `unknown community store: <kind>` (the site then logs it
and uses a memory store).

## Blog

The **Blog** tab is the last item on the left of the navbar. Posts are files in
`docs/site/blog/`, named `YYYY-MM-DD-<slug>.md` (or `.mdx`), with this front
matter:

```yaml
---
title: "Welcome to the blog"
description: A one-sentence summary of the post.
tags: []
date: 2026-10-16
---
```

Everything above the `<!-- truncate -->` line (`{/* truncate */}` in `.mdx`)
is the excerpt on the list page. Posts are parsed as MDX like the docs, `.md`
and `.mdx` alike, so they can use the site's [components](#components) and
follow the same [escaping rules](#docs-are-mdx). Never put email addresses,
cloud identifiers or other private details in a post: the repository and the
site are public. A post is published by merging a pull request, like any other
change, so the strict build checks its links first.

**Every change to the blog is a pull request.** Writing, editing and deleting
a post from the site never changes the site directly: each one opens a pull
request (or downloads a file for one), and the site changes only when that
pull request is merged and the site is rebuilt.

### Write a post, Edit and Delete

- **Write a post.** The blog's post lists (`/blog`, its next pages, the tag
  and author pages) start with a toolbar, outside any post: a **Write a post**
  button that opens the editor (`/blog/editor`) on a new post (or on this
  browser's unfinished new post, never on a draft of a published post) and the
  line "Anyone can write a post: it is published when its pull request is
  merged." It wraps the
  theme's `BlogPostItems` (`src/theme/BlogPostItems/`), which only the list
  pages use, so a post's own page never shows it. The archive
  (`/blog/archive`) is a separate theme page and has no toolbar; the footer's
  **Write a post** link is on every page.
- **Edit** (next to each post's title, in the lists and on the post's page)
  opens the editor on that post: `/blog/editor?post=<repo path>`, for example
  `/blog/editor?post=docs/site/blog/2026-10-16-welcome-to-the-blog.md` (a path
  inside `docs/site/blog/` alone also works). If this browser has a draft of
  the post, the editor opens the draft and offers to discard it for the
  published version. A `?post=` that is not a published post shows a message
  and opens the editor as usual. The address follows the open post, so a
  reload opens the same one. Editing ends with **Open pull request**, as
  below.
- **Delete** opens a dialog (keyboard accessible: the focus stays inside it,
  Escape or Cancel closes it) that names the post's title and file. With a
  GitHub token, **Open a pull request to delete it** runs
  `openBlogDeletePullRequest` from `src/lib/github.mjs`, shows each step, and
  ends with the pull request's link:
  1. it reads the repository (push access?) and the tip of `main`;
  2. it reads the post at that commit for its sha; a post that is not on
     `main` stops here ("post not found on main"), before any branch is made;
  3. with push access it creates a branch `blog/delete-<slug>-<yyyymmddHHMMss>`
     from `main` in the repository; without it, it forks the repository (or
     reuses the fork), waits for it, syncs it and creates the branch there;
  4. it deletes `docs/site/blog/<file>` on that branch (with the sha);
  5. it opens a pull request "Delete blog post: <title>" against `main`.

  The post stays on the site until that pull request is merged; the dialog
  says so. Without a token the dialog shows the token prompt and a link to
  delete the file on GitHub instead. Only `.md` and `.mdx` files under
  `docs/site/blog/` can be deleted this way: any other path is refused before
  a request is made. Error messages never contain the token.
- Posts map to their files with `repoPathForDoc(metadata.source)`
  (`@site/blog/x.md` is `docs/site/blog/x.md`), the same key as the page-meta
  plugin's `blogSources`.

### The blog editor (`/blog/editor`)

The editor writes a post in the browser, with a **live preview on the right**
(stacked under the editor on narrow screens).

- **Open** an existing post (its source is embedded in the build, so it is the
  version of the deployed site) or **start a new one** from a template with the
  front matter above. A toggle switches between `md` and `mdx` (the file
  extension and the truncate marker; both are parsed as MDX). A post's
  **Edit** button opens the editor on that post directly (`?post=`).
- The preview compiles the text as MDX with `@mdx-js/mdx` in the browser
  (loaded only when the editor opens), as the build does for both `md` and
  `mdx` posts, and renders it with the site's own MDX components, so it looks
  like the published post. HTML comments such as `<!-- truncate -->` are
  accepted, as in the build. Compile errors are shown in the preview pane and
  never break the page. The preview runs only in the browser. It has no
  GitHub-flavoured Markdown or directive plugins, so tables, admonitions
  (`:::note`) and imports are rendered only in the published build (the pane
  says so).
- MDX can contain JavaScript expressions, and the preview runs them in the
  page. Only paste MDX you trust.
- **Drafts** are saved automatically for each file in the community store (this
  browser only) and can be reopened later.
- **Download** saves the file, ready to add to `docs/site/blog/` in a pull
  request.
- **Open pull request** appears when a GitHub token is set (without one, the
  editor shows the "Add a GitHub token in the account menu" prompt). It uses
  the GitHub REST API from the browser:
  1. it reads the repository to see whether the token can push to it;
  2. with push access it creates a branch `blog/<slug>-<yyyymmddHHMMss>` from
     `main` in the repository; without it, it forks the repository (or reuses
     the fork), waits for the fork, syncs it with `main` and creates the branch
     there;
  3. it commits the post to `docs/site/blog/<file>` on that branch;
  4. it opens a pull request against `main` and shows its link.

  The pull request is reviewed and merged like any other: an edit is
  published, like a new post, only when it is merged. The preview is a
  convenience: only the build checks links, so read the pull request's Vercel
  preview before merging.

## Privacy: what is stored where

| Data | Stored | Sent to |
| --- | --- | --- |
| Signed-in GitHub profile (login, id, name, avatar URL, profile URL) | The signed `rid_session` cookie (HttpOnly, 400 days) and, without the id, `localStorage` | The site's own `/api/auth/*` functions and `/api/community`, with each request to them (the cookie) |
| Accounts used in this browser (login, name, avatar URL) | `localStorage` | Nowhere |
| GitHub OAuth token (from signing in) | Not stored: the callback function uses it once to read the profile, then drops it | `api.github.com`, from the server |
| Typed username (only where sign-in is not available), name, avatar URL, profile URL | `localStorage` | `api.github.com`: the username, to read the public profile |
| GitHub token (optional) | `localStorage`, this device only | `api.github.com`, as the `Authorization` header; where GitHub sign-in is not available and a typed username is set, this site's `/api/community` with each like or comment, only to confirm the login (never stored there) |
| Guest id (random UUID) | `localStorage` (`rid-community:v1:guest-id`), this browser only | This site's `/api/community` as `X-Guest-Id`, which keeps only a 16-hex SHA-256 prefix of it (the guest key) |
| Likes and comments (shared store) | The `community-data` branch of this public repository: login, name, avatar URL (or "Guest" and the guest key), comment text, dates | Everyone: they are public, and stay in the branch history even after a deletion |
| Likes and comments (without the server token), editor drafts | IndexedDB (or `localStorage`, or memory) | Nowhere |
| Page history from GitHub | `localStorage`, one hour per page (people only; no emails) | `api.github.com`: the page's file path, to read its commits |
| Blog post sent with **Open pull request** | Not stored by the site | `api.github.com`, as a commit and a pull request |
| Post deleted with **Delete** | Not stored by the site | `api.github.com`: the post's path, as a deleting commit and a pull request |
| Colour mode and other Docusaurus settings | `localStorage` | Nowhere |

Requests to `api.github.com`, and the avatar images (served by GitHub), go
from the visitor's browser to GitHub, which sees them like any other visit to
GitHub. Signing in happens on github.com itself, and the sign-in functions
keep no records. The other server code is `/api/community`: it receives the
page id, the action, the comment text, who is writing (the session cookie, a
token, or a guest id) and the client IP (kept in memory, with the guest key,
for the rate limits). The functions log only short status lines (a step and
an HTTP status), never request bodies, headers, codes, cookies or tokens.

## Styling

`src/css/custom.css` holds the theme. With `future.v4.useCssCascadeLayers` the
theme's CSS sits in cascade layers and this file does not, so its rules win
over the theme regardless of specificity. A variable set on `:root` there also
wins in dark mode, so the file sets every changed variable in both the light
block and the `[data-theme='dark']` block.

- **Tokens.** `--ifm-*` variables tune Infima (primary colour, radius, table
  head, blockquote). `--rid-*` variables are the site's own design tokens
  (`--rid-border-color`, `--rid-surface-raised`, `--rid-subtle-background`,
  `--rid-primary-soft`, `--rid-primary-strong`, `--rid-on-primary`,
  `--rid-shadow-sm`/`-md`, `--rid-radius-sm`/`-md`/`-lg`, the hero
  background). Components use them in their CSS modules, so light and dark
  mode are defined in one place.
- **Navbar and footer.** A hairline under the navbar, links with a soft pill
  on hover, and a deep blue footer.
- **Tables and diagrams.** Wide tables scroll inside the article
  (`display: block; overflow-x: auto`), cells align to the top, headers are
  start-aligned unless the Markdown sets an alignment, and Mermaid diagrams
  are centred and scroll when too wide.
- **Focus.** Every link, button and form field shows a 2 px ring in the
  primary colour when focused from the keyboard (`:focus-visible`).
- **Utility classes.** `rid-avatar` (a round GitHub avatar, `<img>` or initials
  in a `<span>`) with sizes `rid-avatar--xs` (20 px), `--sm` (24 px), `--md`
  (32 px, the default), `--lg` (40 px) and `--xl` (48 px); `rid-avatar-stack`
  (an overlapping row of avatars); `rid-sr-only` (visually hidden, read by
  screen readers); `rid-muted-note` (small muted notes such as "Saved in this
  browser").

## Image and diagram zoom

Every Markdown image (`![alt](src)`) and every Mermaid diagram, in the docs,
the blog and the pages, can be enlarged. It is built with pointer events and
CSS, with no extra dependency. Docs and posts are parsed as MDX (see
[Docs are MDX](#docs-are-mdx)), and MDX renders a Markdown image with the
`img` entry of the MDX components and a ```` ```mermaid ```` block with their
`mermaid` entry: the theme's map points those at the two wrappers, and the
site's `src/theme/MDXComponents.tsx`, which adds the rich components, keeps
them (it spreads the original map and replaces neither).

- **Full screen.** Click the image or diagram, or Tab to it and press Enter.
  It opens over the dimmed page, fitted to the screen. Zoom with the mouse
  wheel or a trackpad pinch (at the cursor), two fingers on a touch screen, a
  double click or double tap (fit and 2x), or the buttons (zoom in, zoom out,
  fit, 100 %). Drag to move in any direction; the image cannot be dragged off
  the screen. Keys: `+` and `-` zoom, `0` fits, `1` is the actual size, the
  arrows move (Shift: further), Escape closes. A click beside the image (not
  the end of a drag) or the Close button also closes it, and focus goes back
  to the image. Zoom runs from 0.5x to 8x the fitted size (images: up to 4x
  their natural size when that is larger), and 100 % is always reachable.
  Images also have **Open original** (a new tab).
- **In the page.** Hovering an image or diagram (or tabbing to it) shows a
  small toolbar: zoom in, zoom out, reset, full screen and download. Zoomed in
  place, the element keeps its size and position in the page and its content
  moves inside that box (drag, arrow keys). The mouse wheel zooms in place
  only with Ctrl or Cmd held, or while the zoomed element has focus (after you
  drag it or use its toolbar), so the page still scrolls normally.
- **Download in high quality.** Images: the original file, never a resized
  copy, named after the file (`/img/x.png` is `x.png`; an image Docusaurus
  inlined, under 10 KB, is named after its alt text). Another site's image is
  fetched when that site allows it (CORS); otherwise it opens in a new tab
  with the note "Use Save image as… to download". Diagrams: **SVG** (vector,
  with the page's computed styles inlined so it looks the same anywhere) or
  **PNG** (3x the size it is shown at in the page, at least 2000 px on the
  longest side, on a white or dark background that matches the colour mode;
  the same on every screen, whatever its pixel density). Diagram files are
  named after the page and the diagram's position on it, for example
  `overview-how-it-works-diagram-1.png`.
- **Opting out.** `![alt](src "no-zoom")` or the class `no-zoom` keeps an image
  plain. An image inside a link keeps its link and gets no zoom.
- **A JSX `<img>` tag** (in any doc or post, since all are MDX) is not passed
  through the theme, so it is not zoomable. For a zoomable image use
  `![alt](src)`, or `import Img from '@theme/MDXComponents/Img';` and
  `<Img src="..." alt="..." />`.
- The server-rendered page shows the image exactly as without the zoom (also
  without JavaScript); the button and the toolbar are added once the image has
  loaded. The zoom button's label is `Zoom image: <alt text>`.

The math (fit, zoom at a point, pan within bounds, wheel and pinch) is in
`src/lib/panzoom.mjs` and the download helpers in `src/lib/download.mjs`, both
unit-tested (`test/panzoom.test.mjs`, `test/download.test.mjs`).

## Writing docs

- Add or edit Markdown under `docs/` (not under `docs/site/`). Every file is
  parsed as **MDX**, so read [Docs are MDX](#docs-are-mdx) below before
  writing `{` or `<` in text.
- A new page must be added to `sidebars.ts`, using its path under `docs/`
  without the extension (for example `architecture/roadmap`). Give it a short
  `label` there if its H1 is long.
- Link to other docs with relative `.md` paths (`../architecture/roadmap.md`,
  `postgres-warehouse.md#known-limitations`). Link to code with relative paths
  too (`../../sql/control/001_pipeline_control.sql`); the site turns those into
  GitHub links on the `main` branch. A code link must point at a file that is
  committed to git. The build only checks that the path exists on disk, so a
  link to an ignored or uncommitted local file passes a local build, fails the
  Vercel build (whose checkout has tracked files only) and would 404 on GitHub.
- ```` ```mermaid ```` code blocks render as diagrams.
- The build fails on a broken page link, a broken `#anchor` or a link to a repo
  file that does not exist, so `npm run build` is the check to run before a
  pull request. `npm run check:mdx` is the quick check for MDX syntax.
- Never put cloud project IDs, bucket names, account emails or other private
  identifiers in a doc: the repository and the site are public.

### Docs are MDX

`docusaurus.config.ts` sets `markdown.format: 'mdx'`: every doc and every blog
post is parsed as [MDX](https://mdxjs.com/), Markdown plus JSX, whatever its
extension. That is what lets a page use the components below without an
import.

**The files stay `.md`, on purpose.** GitHub's per-file commit history, which
the page history and its contributors come from, does not follow a rename, so
renaming every file to `.mdx` would cut each page off from its history. The
Python tests (the readiness docs, ADR and register checks), the lead coverage
register, the repository README and the links between docs all name the `.md`
paths. And an `.md` file still reads well on GitHub: GitHub drops the
component tags and shows the Markdown inside them. `.mdx` works too, but
there is no reason to use it for a doc.

MDX reads a few characters differently from GitHub. Fenced code blocks and
`inline code` are never parsed as MDX, so nothing in them needs escaping. In
text:

| Write | For | Why |
| --- | --- | --- |
| `\{` and `\}` | a literal brace, as in `\{doi\}` | `{` starts a JavaScript expression: `{doi}` would fail the build with "doi is not defined" |
| `\<` | a literal `<`, as in `synthetic-\<N>` | `<` starts a JSX tag |
| inline code | code-like text, as in `` `GET /works/{doi}` `` | no escaping needed, and it shows the same everywhere |
| `[https://example.org](https://example.org)` | a bare link | `<https://example.org>` autolinks are not supported |
| `<br />`, `<img ... />`, `className="..."` | raw HTML | HTML must be valid JSX: void tags closed with `/>`, `className` for `class`, `style={{...}}` objects |
| `<!-- comment -->` | a comment | kept valid by `markdown.mdx1Compat.comments`, and invisible on GitHub; `{/* comment */}` works too, but GitHub shows it as text |
| a fenced code block | code | indented (four-space) code blocks do not exist in MDX |

GitHub renders `\{`, `\}` and `\<` as the plain characters, so an escape never
shows on GitHub either. In the lead coverage register pages, keep code-like
examples with braces in inline code rather than escaping them: the register
tests match the lead's wording character for character, and a backslash would
change it.

**`npm run check:mdx`** (`scripts/check-mdx.mjs`) compiles every doc and blog
post with the same remark plugins as the build (front matter, directives,
GitHub-flavoured Markdown, HTML comments) and prints `file:line:column` for
each problem: a syntax error, a `{...}` that would run as JavaScript, or a
component that is neither registered nor imported. `npm test` runs it over the
real docs too. `node scripts/check-mdx.mjs path/to/file.md` checks one file.

### Components

These work in any doc or blog post without an import. They are registered in
`src/theme/MDXComponents.tsx` (which extends the theme's own list) and live in
`src/components/Rich/`, each with a CSS module that follows the light and dark
tokens in `custom.css`. They render on the server and need no browser APIs.

Three rules keep a page readable on GitHub, where the tags are dropped:

1. Leave a blank line after an opening tag and before a closing tag, so the
   content between them is parsed as Markdown (on the site and on GitHub).
2. Do not indent the content inside a component: four spaces would make a code
   block on GitHub.
3. Keep the words that matter in the content, not in attributes: GitHub does
   not show attributes.

The Python docs tests read these files as text. Wrapping a section in a
component is fine, but do not put a status word that a test checks inside a
`<Badge>` in the middle of a sentence, and keep the tables and headings that
the tests parse as they are.

**Cards** link to other pages in a grid. In the Markdown form, the card's text
starts with a Markdown link, which becomes the card's title and its link (the
`\` ends the line on GitHub):

```mdx
<Cards>
<Card icon="flow">

[How it works](how-it-works.md)\
The end-to-end data flow, one stage at a time

</Card>
<Card title="Status" href="status.md#environment-readiness" icon="status" description="Readiness per environment." />
</Cards>
```

The second card uses props: `title`, `href`, `icon` and `description` (or text
as children). `href` takes a relative doc path like a Markdown link; it is
resolved at build time by `src/remark/jsx-links.mjs`, and a broken one fails
the build. A site route (`/blog`) or a URL works too. The whole card is
clickable. Icons are those of the home page (`flow`, `status`, `roadmap`,
`decision`, `register`, `terminal`, `book`, `database`, `laptop`, `server`).

**Steps** draw numbered vertical steps. Wrap a Markdown ordered list (it stays
a numbered list on GitHub), or use `<Step>` elements, whose content can include
headings. `numbered={false}` draws dots instead of numbers, for stages whose
names carry their own numbers (How it works):

```mdx
<Steps>

1. **Install.** Run `make install`.
2. **Test.** Run `make test`.

</Steps>

<Steps numbered={false}>
<Step>

## Raw landing (Step 09)

Text of the stage.

</Step>
</Steps>
```

**Callout** highlights a note: `type` is `note` (default), `tip`, `info`,
`warning` or `danger`, and `title` is optional. The theme's admonitions
(`:::note` ... `:::`) work as well, but on GitHub their `:::` lines show as
text.

```mdx
<Callout type="warning" title="Not deployed">

Nothing here runs in production.

</Callout>
```

**Badge** shows a status label in a coloured pill: green for proven
(`VERIFIED`, `MET`, `MEASURED`), amber for partial or unverified (`PARTIAL`,
`CLOUD_UNVERIFIED`, `SEMANTIC_ONLY`), red for missing or blocking (`NOT_MET`,
`NOT_READY`, `BLOCKED`), blue for defined or local (`CONTRACT_DEFINED`,
`DEMONSTRATED_LOCAL`, `READY_TO_VALIDATE`) and grey for the rest (`DEFERRED`,
`OUT_OF_SCOPE`, `UNKNOWN`). The colour comes from the text (or a `status`
prop) through `src/lib/badgeTone.mjs`; `tone="danger"` overrides it. Markdown
inside is kept, so GitHub still shows the label as code or bold:

```mdx
| Domain | DEV |
| --- | --- |
| `GCS_RAW_LANDING` | <Badge>**VERIFIED**</Badge> |

A <Badge>`NOT_READY`</Badge> environment cannot be promoted.
```

**Tabs** and **TabItem** are the theme's tabs. Tabs with the same `groupId`
switch together across the site:

````mdx
<Tabs groupId="shell">
<TabItem value="powershell" label="PowerShell" default>

```powershell
.\.venv\Scripts\Activate.ps1
```

</TabItem>
<TabItem value="bash" label="Linux or macOS">

```bash
source .venv/bin/activate
```

</TabItem>
</Tabs>
````

**Details** is the theme's collapsible block; `<details>` with a `<summary>`
works the same and is native on GitHub:

```mdx
<details>
<summary>Raw output</summary>

The long part.

</details>
```

**FileTree** draws a nested Markdown list as a file tree; an item with a
nested list is a folder:

```mdx
<FileTree>

- docs/
  - overview/
    - introduction.md
  - site/
- README.md

</FileTree>
```

**Kbd** marks a key: `<Kbd>Ctrl</Kbd> + <Kbd>S</Kbd>` (`<kbd>` works on
GitHub too).

Where they are used today: the introduction (Cards, a Callout, Steps), How it
works (its stages as Steps with dots), Getting started (PowerShell and
Linux/macOS Tabs), Status (a Callout and Badges in the readiness matrix), the
Glossary (Badges for the readiness labels), the roadmap (Callouts) and the ADR
index (its required sections as Steps).

## Deploying on Vercel

The repository root has a `vercel.json` that builds this folder as a static
site, so the project can be imported with the default settings:

1. In Vercel, choose **Add New... > Project** and import the GitHub repository.
2. Leave **Root Directory** as the repository root. `vercel.json` sets the
   framework to none (Other), the install command
   (`npm ci --prefix docs/site`), the build command
   (`npm run build --prefix docs/site`) and the output directory
   (`docs/site/build`).
3. Deploy. Every push to `main` then updates production, and each pull request
   gets a preview URL.

The site's canonical URL comes from `SITE_URL` when set, otherwise from the
`VERCEL_PROJECT_PRODUCTION_URL` that Vercel provides on every build, otherwise
`http://localhost:3000`. Vercel exposes that variable only while the project's
**Automatically expose System Environment Variables** setting is on (the
default). After importing, check that it is on, or set `SITE_URL`; otherwise
the sitemap and canonical links point at `localhost`. Set `SITE_URL` in the
Vercel project's environment variables when a custom domain is added.

Besides the static build, Vercel deploys the repository-root `api/` folder as
Node.js functions (no dependencies; `api/_lib/` is not a route):

- `/api/community`, shared likes and comments. It needs one secret,
  `COMMUNITY_GITHUB_TOKEN` (see [Setup for maintainers](#setup-for-maintainers));
  without it the function answers 503 and the site keeps likes and comments in
  each browser. Its commits never start a build: the data branch carries its
  own root `vercel.json` (created with the branch) that sets
  `git.deploymentEnabled` to false, because Vercel reads that file from the
  pushed commit. The root `vercel.json` on `main` also lists `community-data`
  under `git.deploymentEnabled`.
- `/api/auth/login`, `/api/auth/callback` and `/api/auth/me`, the optional
  GitHub sign-in. They need three variables (see
  [Setting it up](#setting-it-up-vercel)); without them the site works the
  same, with everyone as a guest.

Every other GitHub API call is made by each visitor's browser.

The site has no search box yet. Adding one needs a search plugin dependency
(for example a local-search plugin at a pinned version), which is a separate
change.
