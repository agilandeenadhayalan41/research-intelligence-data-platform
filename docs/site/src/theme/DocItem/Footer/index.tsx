// Wraps the doc footer: the original footer (tags, edit link), then the page
// history and the likes and comments.

import React from 'react';
import Footer from '@theme-original/DocItem/Footer';
import type FooterType from '@theme/DocItem/Footer';
import type {WrapperProps} from '@docusaurus/types';
import {useDoc} from '@docusaurus/plugin-content-docs/client';
import PageHistory from '@site/src/components/PageHistory';
import PageCommunity from '@site/src/components/PageCommunity';
import {repoPathForDoc} from '@site/src/lib/github.mjs';

type Props = WrapperProps<typeof FooterType>;

export default function FooterWrapper(props: Props): React.JSX.Element {
  const {metadata} = useDoc();
  const repoPath = repoPathForDoc(metadata.source);
  return (
    <>
      <Footer {...props} />
      <PageHistory repoPath={repoPath} />
      <PageCommunity pageId={repoPath ?? metadata.permalink} />
    </>
  );
}
