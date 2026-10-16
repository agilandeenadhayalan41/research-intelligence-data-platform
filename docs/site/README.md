# Documentation site

This folder is a [Docusaurus 3](https://docusaurus.io/) site that publishes the
repository's `docs/` folder. It reads the Markdown **in place**: the pages are
the files under `docs/overview/`, `docs/architecture/` and the other folders of
`docs/`, so the same files render on GitHub and on the site. Nothing is copied
or moved.

On top of the docs the site has a home page, a blog with an in-browser editor,
and a few community features on every page: reading time, who last changed the
page, the people who changed it, likes and comments. The site is a **static
build with no server and no database**: everything a visitor adds stays in
their own browser (see [Privacy](#privacy-what-stays-in-the-browser)).

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
| `src/theme/Root.tsx` | Wraps the site in the GitHub-user and community-store providers |
| `src/theme/NavbarItem/ComponentTypes.tsx`, `src/components/GitHubUserNavbarItem/` | The `custom-githubUser` navbar item: avatar and username, or "Sign in" |
| `src/theme/DocItem/Content/`, `src/theme/DocItem/Footer/`, `src/theme/BlogPostItem/Footer/` | Wrappers around the theme components that add the page metadata, history and community blocks |
| `src/components/PageMetaBar/`, `PageHistory/`, `PageCommunity/` | Reading time and last update; contributors; likes and comments |
| `src/lib/storage.mjs` | The community store (likes, comments, editor drafts) behind one adapter interface |
| `src/lib/github.mjs` | GitHub REST helpers: page commits (people only, `isAutomationAuthor`), public profiles, the blog pull-request flows (add or update a post, delete a post) |
| `src/lib/blogPosts.mjs` | Links between the blog and the editor: `blogEditorUrl(repoPath)` and the editor's `?post=` parsing |
| `src/lib/readingTime.mjs` | Word count and minutes to read for a Markdown source |
| `src/lib/GitHubUserContext.tsx`, `src/lib/CommunityStoreContext.tsx` | React hooks `useGitHubUser()` and `useCommunityStore()` |
| `test/` | Unit tests for `src/lib/*.mjs` (`node:test`, no network) |
| `src/remark/repo-links.mjs` | Rewrites relative links that leave `docs/` (for example `../../sql/...`) to GitHub URLs |
| `src/css/custom.css` | Theme colours and design tokens, navbar, footer, table and diagram rules, avatar utility classes, focus rings, dark mode |
| `static/img/logo.svg` | Navbar logo and favicon |

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
npm test          # unit tests for src/lib (node --test test/*.test.mjs)
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

The navbar (right side) has a **Sign in** button. Signing in is deliberately
simple: type a GitHub username, and the site fetches that account's **public**
profile from `https://api.github.com/users/<login>` and keeps the login, name,
avatar URL and profile URL in `localStorage`, so it survives a reload. Signed
in, the navbar shows the avatar and username, and the menu has **Sign out**.

- There is no password, no OAuth and no backend. **It is not authentication**:
  the site cannot check that the person typing a username owns that account.
  The username only labels likes and comments in this browser.
- Everything works signed out. Likes and comments are then made as `guest`.

### The optional GitHub token

The sign-in menu (the account menu) also has an optional **GitHub token**
field. The token is used for three things only: the blog editor's **Open pull
request**, the blog's **Delete** (which opens a pull request too), and higher
GitHub API rate limits for the page history. Without a token, both pull
request buttons show the same prompt: "Add a GitHub token in the account
menu".

- It is kept in `localStorage` **on this device only** and is sent nowhere but
  `https://api.github.com`, as the `Authorization` header. Error messages never
  include it.
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
**5,000 an hour with a token** for the REST API. The site makes one request per
sign-in and at most one per page and hour for the page history (the cache
absorbs the rest). When the limit is reached, pages fall back to the
build-time history and sign-in reports "rate limited"; adding a token raises
the limit.

## Likes and comments

Each doc page and blog post has a like button with a count, and comments with
the author's avatar, name and date. Authors can delete their own comments (the
same GitHub login, or `guest` for comments written signed out). A comment is
1 to 5,000 characters.

Likes, comments and editor drafts are **saved in this browser only**. Other
visitors do not see them, and they are not shared between browsers or
devices. The page says so next to the buttons ("Saved in this browser").
Clearing the site's data in the browser deletes them.

## The storage adapter

All three kinds of data go through one interface, `CommunityStore`, in
`src/lib/storage.mjs`. Components never touch IndexedDB or `localStorage`
directly; they call `useCommunityStore()` and the methods below (all async).

| Method | Returns |
| --- | --- |
| `getLikes(pageId)` | `{count, likedBy}` |
| `toggleLike(pageId, userKey)` | `{count, likedBy}`; `userKey` is the GitHub login or `'guest'` |
| `listComments(pageId)` | comments, oldest first: `{id, pageId, author: {login, name, avatarUrl}, body, createdAt}` |
| `addComment(pageId, {author, body})` | the new comment; throws when the trimmed body is not 1-5000 characters |
| `deleteComment(pageId, id, userKey)` | `true` when the comment was the user's and was deleted |
| `getDraft(key)`, `saveDraft(key, {content, format})` | a draft `{key, content, format, updatedAt}` (or `null`) |
| `listDrafts()`, `deleteDraft(key)` | drafts, newest first; `true` when deleted |
| `backend()` | where the data is kept: `'indexedDB'`, `'localStorage'` or `'memory'` (used for the "Saved in this browser" note; a remote store returns its own value, e.g. `'remote'`) |

`createCommunityStore({kind})` picks the implementation. `kind` comes from
`customFields.communityStore` in `docusaurus.config.ts`; today it is
`'browser'`, which uses:

1. **IndexedDB**: database `rid-community`, version 1, object stores `likes`,
   `comments` and `drafts`;
2. otherwise **`localStorage`**, keys prefixed `rid-community:v1:`;
3. otherwise an **in-memory** store (private windows that block storage, and
   server-side rendering), which is lost on reload.

### Adding a remote store later

Shared likes and comments need a service and a database outside this static
site. They can be added without changing the components that call the store:

1. Build an HTTP API (any host) with one endpoint per method above, and a
   database behind it. Because the site is static, the API is a separate origin:
   allow the site's origin with CORS.
2. **Verify identity on the server.** The browser sign-in proves nothing, so
   the API must authenticate users itself (for example GitHub OAuth, verified
   server-side) before it records a like or a comment or deletes one. Add
   moderation, spam protection and rate limits.
3. In `src/lib/storage.mjs`, write `createRemoteStore({baseUrl, getToken})`
   that implements the same methods with `fetch`, and return it from
   `createCommunityStore` when `kind === 'remote'` (any other kind throws
   `unknown community store: <kind>`, which is where it plugs in). Implement
   `backend()` too: components call it. Keep drafts local if they should stay
   private.
4. Set `communityStore: 'remote'` (and the API URL, for example a new
   `customFields` entry) in `docusaurus.config.ts`. Pass the API URL from
   `customFields` to the store in `makeStore` in
   `src/lib/CommunityStoreContext.tsx` (for example
   `createCommunityStore({kind, baseUrl: siteConfig.customFields.communityApi})`,
   forwarded to `createRemoteStore`), and add the new `backend()` value to
   `CommunityBackend` there and to `storageNote()` in
   `src/components/PageCommunity/index.tsx`, which holds the "Saved in this
   browser" wording. Add tests next to `test/storage.test.mjs` with a mocked
   `fetch`.

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
is the excerpt on the list page. `.md` posts are CommonMark like the docs;
`.mdx` posts can use JSX and the site's components. Never put email addresses,
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
  front matter above. A toggle switches between `md` and `mdx`. A post's
  **Edit** button opens the editor on that post directly (`?post=`).
- The preview compiles the text with `@mdx-js/mdx` in the browser (loaded only
  when the editor opens) and renders it with the site's own MDX components, so
  it looks like the published post. Compile errors are shown in the preview
  pane and never break the page. The preview runs only in the browser.
  It has no GitHub-flavoured Markdown or directive plugins, so tables,
  admonitions (`:::note`), imports and, in `md`, HTML tags are rendered only
  in the published build (the pane says so).
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

## Privacy: what stays in the browser

| Data | Stored | Sent to |
| --- | --- | --- |
| GitHub username, name, avatar URL, profile URL | `localStorage` | `api.github.com`: the username, to read the public profile |
| GitHub token (optional) | `localStorage`, this device only | `api.github.com` only, as the `Authorization` header |
| Likes, comments, editor drafts | IndexedDB (or `localStorage`, or memory) | Nowhere (until a remote store is added) |
| Page history from GitHub | `localStorage`, one hour per page (people only; no emails) | `api.github.com`: the page's file path, to read its commits |
| Blog post sent with **Open pull request** | Not stored by the site | `api.github.com`, as a commit and a pull request |
| Post deleted with **Delete** | Not stored by the site | `api.github.com`: the post's path, as a deleting commit and a pull request |
| Colour mode and other Docusaurus settings | `localStorage` | Nowhere |

Requests to `api.github.com`, and the avatar images (served by GitHub), go
from the visitor's browser to GitHub, which sees them like any other visit to
GitHub. The site itself has no server that receives anything.

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

## Writing docs

- Add or edit Markdown under `docs/` (not under `docs/site/`). `.md` files are
  parsed as CommonMark, the same way GitHub renders them, so `<` and `{` need
  no escaping. Use `.mdx` only when a page needs React components.
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
  pull request.
- Never put cloud project IDs, bucket names, account emails or other private
  identifiers in a doc: the repository and the site are public.

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

The community features need no environment variables, secrets or server
functions: the static build is the whole site, and the GitHub API calls are
made by each visitor's browser.

The site has no search box yet. Adding one needs a search plugin dependency
(for example a local-search plugin at a pinned version), which is a separate
change.
