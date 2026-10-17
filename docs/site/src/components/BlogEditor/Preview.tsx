import React, {
  Component,
  useEffect,
  useRef,
  useState,
  type ComponentType,
  type ErrorInfo,
  type MouseEvent,
  type ReactNode,
} from 'react';
import clsx from 'clsx';
import * as runtime from 'react/jsx-runtime';
import MDXComponents from '@theme/MDXComponents';
import type {MDXComponents as MDXComponentMap, MDXContent} from 'mdx/types';

import {htmlCommentsToMdx} from '@site/src/lib/htmlComments.mjs';

import type {PostFrontMatter} from './frontMatter';
import styles from './styles.module.css';

export type PostFormat = 'md' | 'mdx';

export type CompileStatus =
  | {state: 'compiling'}
  | {state: 'ok'}
  | {state: 'error'; message: string};

interface PreviewProps {
  /** Post source with the front matter blanked out (see splitFrontMatter). */
  body: string;
  frontMatter: PostFrontMatter | null;
  readingMinutes: number;
  /** Told whenever a compile starts, succeeds or fails. */
  onStatus?: (status: CompileStatus) => void;
}

interface Compiled {
  Content: MDXContent;
  components: MDXComponentMap;
  version: number;
}

interface CompileError {
  message: string;
  location: string | null;
  hint: string | null;
}

const DEBOUNCE_MS = 300;

type MdxModule = typeof import('@mdx-js/mdx');
let mdxModule: Promise<MdxModule> | null = null;

/** Loads the MDX compiler on first use (a separate chunk, browser only). */
function loadMdx(): Promise<MdxModule> {
  if (!mdxModule) {
    mdxModule = import('@mdx-js/mdx').catch((error: unknown) => {
      mdxModule = null;
      throw error;
    });
  }
  return mdxModule;
}

const dateFormat = new Intl.DateTimeFormat('en', {dateStyle: 'long', timeZone: 'UTC'});

/**
 * Live preview of a post: compiles the source with @mdx-js/mdx as MDX, the
 * way the site's build parses every post (.md or .mdx), and renders it with
 * the site's MDX components, so it looks like the published post. Compile and
 * render errors are shown in the pane. Must only be rendered in the browser.
 */
export default function Preview({
  body,
  frontMatter,
  readingMinutes,
  onStatus,
}: PreviewProps): ReactNode {
  const [compiled, setCompiled] = useState<Compiled | null>(null);
  const [error, setError] = useState<CompileError | null>(null);
  const [loading, setLoading] = useState(true);
  const sequence = useRef(0);
  const statusRef = useRef(onStatus);
  statusRef.current = onStatus;

  useEffect(() => {
    const version = sequence.current + 1;
    sequence.current = version;
    statusRef.current?.({state: 'compiling'});
    const timer = window.setTimeout(
      () => {
        void compile(body).then((result) => {
          if (sequence.current !== version) {
            return; // A newer edit is being compiled.
          }
          setLoading(false);
          if ('error' in result) {
            setError(result.error);
            statusRef.current?.({state: 'error', message: result.error.message});
          } else {
            setError(null);
            setCompiled({...result, version});
            statusRef.current?.({state: 'ok'});
          }
        });
      },
      // The first compile runs at once; later ones wait for a pause in typing.
      version > 1 ? DEBOUNCE_MS : 0,
    );
    return () => window.clearTimeout(timer);
  }, [body]);

  const title = frontMatter?.title ?? null;
  const date = frontMatter?.date ?? null;

  return (
    <div className={styles.previewInner}>
      {error && (
        <div className={styles.compileError} role="alert">
          <p className={styles.compileErrorTitle}>
            MDX error
            {error.location ? ` at ${error.location}` : ''}
          </p>
          <pre className={styles.compileErrorMessage}>{error.message}</pre>
          {error.hint && <p className={styles.compileErrorHint}>{error.hint}</p>}
          {compiled && (
            <p className={styles.compileErrorHint}>
              Below is the last version that compiled.
            </p>
          )}
        </div>
      )}
      <article
        className={clsx(styles.previewArticle, error && compiled && styles.previewStale)}
        aria-busy={loading}>
        <header className={styles.previewHeader}>
          {/* Not an <h1>: the editor page has its own. */}
          <p className={styles.previewTitle}>{title ?? 'Untitled post'}</p>
          <p className={styles.previewMeta}>
            {date ? <time dateTime={date}>{formatDay(date)}</time> : <span>No date</span>}
            <span aria-hidden="true"> · </span>
            <span>{readingMinutes} min read</span>
          </p>
          {frontMatter?.description && (
            <p className={styles.previewDescription}>{frontMatter.description}</p>
          )}
          {frontMatter && frontMatter.tags.length > 0 && (
            <ul className={styles.previewTags} aria-label="Tags">
              {frontMatter.tags.map((tag) => (
                <li key={tag} className="badge badge--secondary">
                  {tag}
                </li>
              ))}
            </ul>
          )}
        </header>
        {loading && !compiled ? (
          <p className={styles.previewLoading}>Loading the preview…</p>
        ) : (
          compiled && (
            <PreviewBoundary resetKey={compiled.version}>
              <div className="markdown" onClickCapture={openLinksInNewTab}>
                <RenderContent compiled={compiled} />
              </div>
            </PreviewBoundary>
          )
        )}
      </article>
    </div>
  );
}

/**
 * Calls the compiled component as a function instead of rendering
 * <Content />: evaluate() returns a new function on every compile, and React
 * would otherwise remount the whole preview on each keystroke.
 */
function RenderContent({compiled}: {compiled: Compiled}): ReactNode {
  try {
    return compiled.Content({components: compiled.components});
  } catch (error) {
    // A missing component or a failing expression in the post itself. Errors
    // inside the site components are left to PreviewBoundary.
    return <RenderError error={error} />;
  }
}

async function compile(
  body: string,
): Promise<Omit<Compiled, 'version'> | {error: CompileError}> {
  let mdx: MdxModule;
  try {
    mdx = await loadMdx();
  } catch {
    return {
      error: {
        message: 'The MDX compiler could not be loaded.',
        location: null,
        hint: 'Check the network connection and reload the page.',
      },
    };
  }
  const imports: string[] = [];
  try {
    const evaluated = await mdx.evaluate(htmlCommentsToMdx(body, mdx), {
      ...(runtime as unknown as Pick<
        Parameters<MdxModule['evaluate']>[1],
        'Fragment' | 'jsx' | 'jsxs'
      >),
      // The build parses .md posts as MDX too (markdown.format: 'mdx').
      format: 'mdx',
      development: false,
      baseUrl: window.location.href,
      remarkPlugins: [[remarkPreview, {imports}]],
    });
    const components: MDXComponentMap = {
      ...(MDXComponents as unknown as MDXComponentMap),
    };
    for (const name of imports) {
      components[name] = makePlaceholder(name);
    }
    return {Content: evaluated.default, components};
  } catch (error) {
    return {error: describeError(error)};
  }
}

interface MessageLike {
  reason?: unknown;
  line?: unknown;
  column?: unknown;
  message?: unknown;
}

function describeError(error: unknown): CompileError {
  const value = (error ?? {}) as MessageLike;
  const message =
    typeof value.reason === 'string' && value.reason
      ? value.reason
      : typeof value.message === 'string' && value.message
        ? value.message
        : String(error);
  const location =
    typeof value.line === 'number'
      ? `line ${value.line}${typeof value.column === 'number' ? `, column ${value.column}` : ''}`
      : null;
  let hint: string | null = null;
  if (/<!--|html comment/i.test(message)) {
    hint = 'Put an HTML comment on lines of its own, or use {/* comment */} instead.';
  } else if (/acorn|expression|unexpected character|closing tag/i.test(message)) {
    hint =
      'Posts are MDX (.md too): "{" starts a JavaScript expression and "<" starts JSX. Escape them as \\{ and \\< when they are text.';
  }
  return {message, location, hint};
}

// ---------------------------------------------------------------------------
// Remark plugin for the preview

interface MdNode {
  type: string;
  /** Name of an MDX JSX element. */
  name?: string | null;
  children?: MdNode[];
  value?: string;
  lang?: string | null;
  meta?: string | null;
  data?: {
    estree?: {body: EsStatement[]};
    hName?: string;
    hProperties?: Record<string, unknown>;
  };
}

interface EsStatement {
  type: string;
  source?: unknown;
  specifiers?: Array<{local?: {name?: string}}>;
}

/**
 * - ESM imports cannot be loaded in the browser preview, so they are removed
 *   and each imported name is recorded (it is then rendered as a placeholder).
 * - ```mermaid code blocks become the theme's Mermaid diagram, as in the build.
 * - <details> becomes the theme's <Details>, as Docusaurus does in MDX (MDX
 *   does not swap JSX written with a lowercase name for a component).
 */
function remarkPreview({imports}: {imports: string[]}) {
  return (tree: MdNode): void => {
    if (Array.isArray(tree.children)) {
      tree.children = tree.children.filter((node) => {
        if (node.type !== 'mdxjsEsm' || !node.data?.estree) {
          return true;
        }
        const program = node.data.estree;
        program.body = program.body.filter((statement) => {
          if (statement.type === 'ImportDeclaration') {
            for (const specifier of statement.specifiers ?? []) {
              const name = specifier.local?.name;
              if (name && !imports.includes(name)) {
                imports.push(name);
              }
            }
            return false;
          }
          // `export ... from '...'` is an import too.
          return !(
            (statement.type === 'ExportNamedDeclaration' ||
              statement.type === 'ExportAllDeclaration') &&
            statement.source
          );
        });
        return program.body.length > 0;
      });
    }
    visitPreviewNodes(tree);
  };
}

function visitPreviewNodes(node: MdNode): void {
  if (node.type === 'mdxJsxFlowElement' && node.name === 'details') {
    node.name = 'Details';
  }
  if (node.type === 'code' && node.lang === 'mermaid') {
    const value = node.value ?? '';
    node.type = 'mermaidCodeBlock';
    delete node.value;
    delete node.lang;
    delete node.meta;
    node.data = {hName: 'mermaid', hProperties: {value}};
    return;
  }
  for (const child of node.children ?? []) {
    visitPreviewNodes(child);
  }
}

function makePlaceholder(name: string): ComponentType<{children?: ReactNode}> {
  function ImportedComponentPlaceholder({children}: {children?: ReactNode}): ReactNode {
    return (
      <div className={styles.placeholder}>
        <p className={styles.placeholderLabel}>
          <code>{`<${name}>`}</code> is imported, and imports are not loaded in the preview.
          It is rendered in the published post.
        </p>
        {children}
      </div>
    );
  }
  ImportedComponentPlaceholder.displayName = `Placeholder(${name})`;
  return ImportedComponentPlaceholder;
}

// ---------------------------------------------------------------------------
// Rendering helpers

/** Links in the preview open in a new tab, so the editor stays open. */
function openLinksInNewTab(event: MouseEvent<HTMLDivElement>): void {
  const target = event.target as Element | null;
  const link = target?.closest?.('a[href]') as HTMLAnchorElement | null;
  if (!link) {
    return;
  }
  const href = link.getAttribute('href') ?? '';
  if (href.startsWith('#')) {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  window.open(link.href, '_blank', 'noopener,noreferrer');
}

function formatDay(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? day : dateFormat.format(date);
}

interface BoundaryProps {
  resetKey: number;
  children: ReactNode;
}

interface BoundaryState {
  error: Error | null;
}

/** Catches errors thrown while rendering the post (for example an unknown component). */
class PreviewBoundary extends Component<BoundaryProps, BoundaryState> {
  override state: BoundaryState = {error: null};

  static getDerivedStateFromError(error: unknown): BoundaryState {
    return {error: error instanceof Error ? error : new Error(String(error))};
  }

  override componentDidUpdate(previous: BoundaryProps): void {
    if (previous.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({error: null});
    }
  }

  override componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // Shown in the pane; nothing to report.
  }

  override render(): ReactNode {
    const {error} = this.state;
    return error ? <RenderError error={error} /> : this.props.children;
  }
}

function RenderError({error}: {error: unknown}): ReactNode {
  const message = error instanceof Error ? error.message : String(error);
  const missing = /Expected component `([^`]+)` to be defined/.exec(message);
  return (
    <div className={styles.compileError} role="alert">
      <p className={styles.compileErrorTitle}>The post compiled, but rendering it failed</p>
      <pre className={styles.compileErrorMessage}>{message}</pre>
      <p className={styles.compileErrorHint}>
        {missing
          ? `The component ${missing[1]} is not defined. Import it in the post, or check its name.`
          : 'Check the JavaScript expressions and components in the post.'}
      </p>
    </div>
  );
}
