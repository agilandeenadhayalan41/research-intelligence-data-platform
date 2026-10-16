---
title: "Welcome to the blog"
description: What this blog is for, and the two ways to write a post for it, the editor in the browser or a pull request.
tags: [blog, contributing]
date: 2026-10-16
---

This blog is for short posts about the Research Intelligence Data Platform:
what changed, what was learned along the way, and notes that are worth reading
but do not belong in a reference page. This first post explains how to write
one.

<!-- truncate -->

## What the blog is, and what it is not

The docs stay the source of truth. What has been validated, and how strongly,
is on the [Status](/overview/status) page. Decisions are recorded in the
[architecture decision records](/adr/). A post can tell the story around a
change and link to those pages, but it does not replace them, and it never
changes a status.

Posts are files in the repository, in `docs/site/blog/`. A post is published
the same way as any other change to the docs: through a pull request that is
reviewed and merged. The site's build checks every link first, so a post with
a broken link is not published.

![How a blog post is published: write it, open a pull request, the build checks every link, review and merge, published on the site](/img/blog/publishing-flow.svg)

The repository and the site are public. Never put email addresses, cloud
identifiers or other private details in a post.

## Write a post in the editor

The [blog editor](/blog/editor) (**Write a post** at the top of the blog, or in
the footer) runs in your browser:

1. Start a new post from the template, or open an existing post.
2. Choose the format: `md` or `mdx`. Both are parsed as MDX, like the docs, so
   both can use the site's components; the format only sets the file extension
   and the truncate marker.
3. Write on the left. The preview on the right shows the post with the site's
   styles as you type, and shows compile errors there instead of breaking the
   page.
4. Save it:
   - **Download** saves the file, ready to add to `docs/site/blog/` in a pull
     request.
   - **Open pull request** commits the post to a new branch and opens a pull
     request against `main` for you. It needs a GitHub token, which you can
     add in the account menu at the right of the navigation bar (the arrow
     next to Sign in, or your avatar). If you cannot push to the repository,
     the editor forks it and opens the pull request from your fork.

Drafts are saved automatically, in this browser only, and you can reopen them
from the editor later. The GitHub token is kept on this device only. It is
used with the GitHub API and, only where GitHub sign-in is not available, to
confirm your name on likes and comments.

## Write a post in a pull request

You can also add the file yourself. Name it `YYYY-MM-DD-<slug>.md` (or `.mdx`)
in `docs/site/blog/`, and start it with this front matter:

```yaml
---
title: "Your post title"
description: A one-sentence summary of the post.
tags: []
date: 2026-10-16
---
```

The text above the `<!-- truncate -->` line is the excerpt on the blog list
page. In an `.mdx` post the marker is `{/* truncate */}`. Then open a pull
request against `main`.

## Edit or delete a post

Each post has **Edit** and **Delete** buttons next to its title. **Edit**
opens the post in the editor; your changes go out as a pull request, like a
new post. **Delete** opens a pull request that deletes the post's file (it
needs the same GitHub token). Either way, nothing changes on the site until
the pull request is merged.

## Likes and comments

Each post has a like button and comments, like the doc pages. They are shared
with everyone and saved in this site's GitHub repository. Anyone can like and
comment right away as a guest; sign in with GitHub (navbar, right) to show
your GitHub name. No token is needed for that.
