import React, {type ReactNode} from 'react';
import BrowserOnly from '@docusaurus/BrowserOnly';
import Link from '@docusaurus/Link';
import Layout from '@theme/Layout';

import BlogEditor from '@site/src/components/BlogEditor';
import styles from '@site/src/components/BlogEditor/styles.module.css';

// The editor uses browser storage, the GitHub API and the MDX compiler, so it
// renders only in the browser (its modules touch no browser API on import).
// The server-rendered page has the heading, the introduction and a loading
// message.
function Editor(): ReactNode {
  return (
    <BrowserOnly
      fallback={
        <div className={styles.loadingEditor} role="status">
          Loading the editor… It needs JavaScript.
        </div>
      }>
      {() => <BlogEditor />}
    </BrowserOnly>
  );
}

export default function BlogEditorPage(): ReactNode {
  return (
    <Layout
      title="Blog editor"
      description="Write a blog post in Markdown or MDX with a live preview, save drafts in this browser, download the file or open a pull request.">
      <main className={styles.page}>
        <header className={styles.pageHeader}>
          <h1 className={styles.pageTitle}>Blog editor</h1>
          <p className={styles.pageIntro}>
            Write a post in Markdown or MDX, with a live preview on the right. Drafts are saved in
            this browser only. When it is ready, download the file or open a pull request; posts
            are published when the pull request is merged. See{' '}
            <Link to="/blog/2026/10/16/welcome-to-the-blog">Welcome to the blog</Link> for how it
            works.
          </p>
        </header>
        <Editor />
      </main>
    </Layout>
  );
}
