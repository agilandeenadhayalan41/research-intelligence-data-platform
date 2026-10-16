// Wraps the doc content: the page meta bar (reading time, last update,
// contributors) goes above it.

import React from 'react';
import Content from '@theme-original/DocItem/Content';
import type ContentType from '@theme/DocItem/Content';
import type {WrapperProps} from '@docusaurus/types';
import {useDoc} from '@docusaurus/plugin-content-docs/client';
import PageMetaBar from '@site/src/components/PageMetaBar';
import {repoPathForDoc} from '@site/src/lib/github.mjs';

type Props = WrapperProps<typeof ContentType>;

export default function ContentWrapper(props: Props): React.JSX.Element {
  const {metadata} = useDoc();
  return (
    <>
      <PageMetaBar repoPath={repoPathForDoc(metadata.source)} />
      <Content {...props} />
    </>
  );
}
