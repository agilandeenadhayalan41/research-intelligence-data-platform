// Remark plugins: resolve link attributes of MDX components like Markdown links.
//
// Docusaurus resolves Markdown links (`[Status](status.md)`) to routes and
// fails the strict build on a broken one, but it never looks inside JSX
// attributes. These two plugins give components the same behaviour, so a doc
// can write `<Card href="status.md#environment-readiness">` with the relative
// `.md` path it would use in Markdown:
//
// 1. `jsxLinksBefore` (in `beforeDefaultRemarkPlugins`, ahead of repo-links)
//    adds a hidden Markdown definition node for each link attribute listed in
//    LINK_ATTRIBUTES whose value is a plain string.
// 2. The default plugins then treat that node like any Markdown link: links
//    that leave docs/ become GitHub URLs (repo-links.mjs), doc paths become
//    routes, and a broken one throws (`onBrokenMarkdownLinks: 'throw'`), with
//    the attribute's line in the message.
// 3. `jsxLinksAfter` (in `remarkPlugins`, after the defaults) copies the
//    resolved URL back into the attribute and removes the helper node.
//
// Site routes (`/overview/status`) and external URLs pass through unchanged;
// the components render them with @docusaurus/Link, so the build's broken-link
// check covers them too.

/** Component name -> the attributes that hold a link. */
export const LINK_ATTRIBUTES = Object.freeze({
  Card: Object.freeze(['href']),
});

const DATA_KEY = 'ridJsxLinkAttribute';

function isJsxElement(node) {
  return node.type === 'mdxJsxFlowElement' || node.type === 'mdxJsxTextElement';
}

function visit(node, callback) {
  callback(node);
  for (const child of node.children ?? []) {
    visit(child, callback);
  }
}

/**
 * @param {{attributes?: Record<string, readonly string[]>}} [options]
 */
export function jsxLinksBefore(options = {}) {
  const attributes = options.attributes ?? LINK_ATTRIBUTES;
  return (tree) => {
    const helpers = [];
    visit(tree, (node) => {
      if (!isJsxElement(node) || !Object.hasOwn(attributes, node.name ?? '')) {
        return;
      }
      for (const attribute of node.attributes ?? []) {
        if (
          attribute.type === 'mdxJsxAttribute' &&
          attributes[node.name].includes(attribute.name) &&
          typeof attribute.value === 'string' &&
          attribute.value !== ''
        ) {
          const id = `rid-jsx-link-${helpers.length}`;
          helpers.push({
            type: 'definition',
            identifier: id,
            label: id,
            url: attribute.value,
            title: null,
            position: attribute.position ?? node.position,
            data: {[DATA_KEY]: attribute},
          });
        }
      }
    });
    tree.children.push(...helpers);
  };
}

export function jsxLinksAfter() {
  return (tree) => {
    tree.children = tree.children.filter((node) => {
      const attribute = node.type === 'definition' ? node.data?.[DATA_KEY] : undefined;
      if (!attribute) {
        return true;
      }
      attribute.value = node.url;
      return false;
    });
  };
}
