// Opens the navbar account menu from anywhere in the page (for example the
// "Sign in with GitHub" link under the comment form), with the token field
// shown. On a wide screen the navbar item opens its popover; on a narrow one
// the mobile sidebar opens on its main menu and the item opens its panel there.

import {useCallback} from 'react';
import {useNavbarMobileSidebar, useNavbarSecondaryMenu} from '@docusaurus/theme-common/internal';
import {useGitHubUser} from '@site/src/lib/GitHubUserContext';

export default function useOpenAccountMenu(): () => void {
  const {requestAccountMenu} = useGitHubUser();
  const mobileSidebar = useNavbarMobileSidebar();
  const secondaryMenu = useNavbarSecondaryMenu();
  return useCallback(() => {
    requestAccountMenu();
    if (mobileSidebar.shouldRender) {
      // On a docs page the sidebar opens on the docs menu; the account item
      // is in the main menu.
      if (secondaryMenu.shown) {
        secondaryMenu.hide();
      }
      if (!mobileSidebar.shown) {
        mobileSidebar.toggle();
      }
    }
  }, [requestAccountMenu, mobileSidebar, secondaryMenu]);
}
