// Wraps the whole site (every page, on the server and in the browser) with
// the community providers: the optional GitHub sign-in and the store for
// likes, comments and blog-editor drafts. Also renders the sign-in toast
// ("Signed in as @login", or why a sign-in did not complete).

import React, {type ReactNode} from 'react';
import {GitHubUserProvider} from '@site/src/lib/GitHubUserContext';
import {CommunityStoreProvider} from '@site/src/lib/CommunityStoreContext';
import AuthNotice from '@site/src/components/AuthNotice';

export default function Root({children}: {children: ReactNode}): React.JSX.Element {
  return (
    <GitHubUserProvider>
      <CommunityStoreProvider>{children}</CommunityStoreProvider>
      <AuthNotice />
    </GitHubUserProvider>
  );
}
