// Adds the `custom-githubUser` navbar item type (the optional GitHub
// sign-in, see src/components/GitHubUserNavbarItem) to the theme's types.

import ComponentTypes from '@theme-original/NavbarItem/ComponentTypes';
import type {ComponentTypesObject} from '@theme/NavbarItem/ComponentTypes';
import GitHubUserNavbarItem from '@site/src/components/GitHubUserNavbarItem';

const types: ComponentTypesObject = {
  ...(ComponentTypes as ComponentTypesObject),
  'custom-githubUser': GitHubUserNavbarItem,
};

export default types;
