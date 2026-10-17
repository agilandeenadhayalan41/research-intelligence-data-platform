// Wraps a blog post's header (title, date, authors): Edit and Delete go next
// to the title, in the post list and on the post's own page.

import React from 'react';
import Header from '@theme-original/BlogPostItem/Header';
import type HeaderType from '@theme/BlogPostItem/Header';
import type {WrapperProps} from '@docusaurus/types';
import {useBlogPost} from '@docusaurus/plugin-content-blog/client';
import BlogPostActions from '@site/src/components/BlogPostActions';
import {repoPathForDoc} from '@site/src/lib/github.mjs';

import styles from './styles.module.css';

type Props = WrapperProps<typeof HeaderType>;

export default function HeaderWrapper(props: Props): React.JSX.Element {
  const {metadata} = useBlogPost();
  return (
    <div className={styles.header}>
      <div className={styles.main}>
        <Header {...props} />
      </div>
      <BlogPostActions repoPath={repoPathForDoc(metadata.source)} title={metadata.title} />
    </div>
  );
}
