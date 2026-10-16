// Wraps the blog post footer. On a post's own page (not in the post list) it
// adds the page history and the likes and comments after the original footer.

import React from 'react';
import Footer from '@theme-original/BlogPostItem/Footer';
import type FooterType from '@theme/BlogPostItem/Footer';
import type {WrapperProps} from '@docusaurus/types';
import {useBlogPost} from '@docusaurus/plugin-content-blog/client';
import PageHistory from '@site/src/components/PageHistory';
import PageCommunity from '@site/src/components/PageCommunity';
import {repoPathForDoc} from '@site/src/lib/github.mjs';

type Props = WrapperProps<typeof FooterType>;

export default function FooterWrapper(props: Props): React.JSX.Element {
  const {metadata, isBlogPostPage} = useBlogPost();
  if (!isBlogPostPage) {
    return <Footer {...props} />;
  }
  const repoPath = repoPathForDoc(metadata.source);
  return (
    <>
      <Footer {...props} />
      <PageHistory repoPath={repoPath} />
      <PageCommunity pageId={repoPath ?? metadata.permalink} />
    </>
  );
}
