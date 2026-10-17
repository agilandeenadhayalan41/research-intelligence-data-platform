// "Write a post" at the top of the blog's post lists (/blog, its pages, tag
// and author pages), outside any post. Rendered by the BlogPostItems wrapper
// (src/theme/BlogPostItems), which a post's own page does not use.

import React, {type ReactNode} from 'react';
import Link from '@docusaurus/Link';
import {BLOG_EDITOR_ROUTE} from '@site/src/lib/blogPosts.mjs';

import styles from './styles.module.css';

export default function WritePostToolbar(): ReactNode {
  return (
    <aside className={styles.toolbar} aria-label="Write a blog post">
      <p className={styles.toolbarText}>
        Anyone can write a post: it is published when its pull request is merged.
      </p>
      <Link className={`button button--primary ${styles.toolbarButton}`} to={BLOG_EDITOR_ROUTE}>
        <svg className={styles.icon} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <path d="M8 3v10M3 8h10" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
        </svg>
        Write a post
      </Link>
    </aside>
  );
}
