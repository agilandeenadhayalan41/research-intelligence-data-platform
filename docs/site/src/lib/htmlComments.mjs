// HTML comments to MDX comments, for the blog editor's browser preview.
//
// The build keeps HTML comments valid in MDX (markdown.mdx1Compat.comments),
// through a parser extension the browser preview does not load. So the
// preview turns each HTML comment into an MDX comment first, keeping the line
// count, so error positions stay the same. That covers a comment block, a
// comment followed by text on the same line, and a comment inside an HTML
// block that starts with a JSX tag. Comments inside code are left alone: a
// CommonMark parse tells them apart. Plain ESM with no browser globals, so it
// also runs in node:test.

/**
 * @typedef {object} OffsetNode
 * @property {string} type
 * @property {OffsetNode[]} [children]
 * @property {{start: {offset?: number}, end: {offset?: number}}} [position]
 */

/**
 * @typedef {object} MdParser
 * @property {(options: {format: 'md'}) => {parse: (body: string) => unknown}} createProcessor
 *   The createProcessor of @mdx-js/mdx (passed in, so the preview can load
 *   the compiler lazily).
 */

/**
 * @param {string} body Markdown or MDX source.
 * @param {MdParser} mdx The @mdx-js/mdx module.
 * @returns {string} The source with every HTML comment outside code written
 *   as `{/* ... *\/}`.
 */
export function htmlCommentsToMdx(body, mdx) {
  if (!body.includes('<!--')) {
    return body;
  }
  const tree = /** @type {OffsetNode} */ (mdx.createProcessor({format: 'md'}).parse(body));
  /** @type {Array<{start: number, end: number, inner: string}>} */
  const comments = [];
  /** @param {OffsetNode} node */
  const visit = (node) => {
    const start = node.position?.start.offset;
    const end = node.position?.end.offset;
    if (node.type === 'html' && start !== undefined && end !== undefined) {
      const raw = body.slice(start, end);
      for (const m of raw.matchAll(/<!--([\s\S]*?)-->/g)) {
        comments.push({start: start + m.index, end: start + m.index + m[0].length, inner: m[1]});
      }
    }
    for (const child of node.children ?? []) {
      visit(child);
    }
  };
  visit(tree);
  let result = body;
  for (const {start, end, inner} of comments.reverse()) {
    result = `${result.slice(0, start)}{/*${inner.replaceAll('*/', '* /')}*/}${result.slice(end)}`;
  }
  return result;
}
