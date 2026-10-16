// Wraps the whole site (every page, on the server and in the browser) with
// the community providers: the optional GitHub sign-in and the store for
// likes, comments and blog-editor drafts.

import React, {type ReactNode} from 'react';
import {GitHubUserProvider} from '@site/src/lib/GitHubUserContext';
import {CommunityStoreProvider} from '@site/src/lib/CommunityStoreContext';

export default function Root({children}: {children: ReactNode}): React.JSX.Element {
  return (
    <GitHubUserProvider>
      <CommunityStoreProvider>{children}</CommunityStoreProvider>
    </GitHubUserProvider>
  );
}
