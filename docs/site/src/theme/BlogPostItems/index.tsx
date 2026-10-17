// Wraps the list of blog posts (the blog's main page and its pages, tag and
// author pages): the "Write a post" toolbar goes above the posts, outside any
// of them. A post's own page does not use this component, so it never shows
// the toolbar.

import React from 'react';
import BlogPostItems from '@theme-original/BlogPostItems';
import type BlogPostItemsType from '@theme/BlogPostItems';
import type {WrapperProps} from '@docusaurus/types';
import WritePostToolbar from '@site/src/components/BlogPostActions/WritePostToolbar';

type Props = WrapperProps<typeof BlogPostItemsType>;

export default function BlogPostItemsWrapper(props: Props): React.JSX.Element {
  return (
    <>
      <WritePostToolbar />
      <BlogPostItems {...props} />
    </>
  );
}
