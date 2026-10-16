// The components MDX content can use without importing them: the theme's own
// (headings, code blocks, Details, admonitions, Mermaid...) plus this site's
// rich components. Every doc and blog post is MDX (markdown.format: 'mdx'),
// so `.md` files can use them too. Keep scripts/check-mdx.mjs
// (GLOBAL_COMPONENTS) in step with this list.
//
// The original map's entries stay as they are, including `img` and `mermaid`:
// they point at the zoom wrappers src/theme/MDXComponents/Img and
// src/theme/Mermaid (lightbox, in-page zoom, downloads), so do not replace
// them here.

import MDXComponentsOriginal from '@theme-original/MDXComponents';
import TabItem from '@theme/TabItem';
import Tabs from '@theme/Tabs';
import {
  Badge,
  Callout,
  Card,
  Cards,
  FileTree,
  Kbd,
  Step,
  Steps,
} from '@site/src/components/Rich';

const MDXComponents = {
  ...MDXComponentsOriginal,
  Badge,
  Callout,
  Card,
  Cards,
  FileTree,
  Kbd,
  Step,
  Steps,
  TabItem,
  Tabs,
};

export default MDXComponents;
