import React, {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import clsx from 'clsx';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import {usePluginData} from '@docusaurus/useGlobalData';
import {newPostTemplate, postFileName} from '@site/src/lib/github.mjs';
import {readingTime} from '@site/src/lib/readingTime.mjs';
import type {Draft} from '@site/src/lib/storage.mjs';
import {useCommunityStore, useDraftsBackend} from '@site/src/lib/CommunityStoreContext';
import {useGitHubUser} from '@site/src/lib/GitHubUserContext';

import {convertTruncateMarker, splitFrontMatter} from './frontMatter';
import Preview, {type CompileStatus, type PostFormat} from './Preview';
import PullRequestPanel, {type RepoConfig} from './PullRequestPanel';
import styles from './styles.module.css';

/** Folder of the blog posts in the repository. */
const BLOG_DIR = 'docs/site/blog';
/** Draft keys: 'blog:<repo path>' for a published post, 'blog-new:<id>' for a new one. */
const POST_DRAFT_PREFIX = 'blog:';
const NEW_DRAFT_PREFIX = 'blog-new:';
const AUTOSAVE_MS = 800;
const NEW_POST_TITLE = 'Untitled post';

interface BlogSource {
  repoPath: string;
  file: string;
  title: string;
  format: PostFormat;
  content: string;
}

interface EditorDoc {
  /** Draft key in the community store. */
  key: string;
  kind: 'new' | 'existing';
  /** Repository path of a published post (null for a new post). */
  repoPath: string | null;
  /** Source of the published post from the build (null when unknown). */
  published: string | null;
  /** The text as it was opened: autosave starts once it changes. */
  baseline: string;
  content: string;
  format: PostFormat;
}

type SaveState =
  | {state: 'idle'}
  | {state: 'pending'}
  | {state: 'saving'}
  | {state: 'saved'; at: string}
  | {state: 'error'; message: string};

interface Notice {
  text: string;
  action?: {label: string; run: () => void};
}

const timeFormat = new Intl.DateTimeFormat('en', {dateStyle: 'medium', timeStyle: 'short'});

/**
 * The blog editor: source on the left, live preview on the right (stacked on
 * narrow screens). Browser only: render it inside <BrowserOnly>.
 */
export default function BlogEditor(): ReactNode {
  const id = useId();
  const store = useCommunityStore();
  const backend = useDraftsBackend();
  const {token} = useGitHubUser();
  const {siteConfig} = useDocusaurusContext();
  const repo = useMemo(() => readRepo(siteConfig.customFields?.repo), [siteConfig]);
  const pageMeta = usePluginData('page-meta') as {blogSources?: unknown} | undefined;
  const sources = useMemo(() => readBlogSources(pageMeta?.blogSources), [pageMeta]);

  const [doc, setDoc] = useState<EditorDoc | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [save, setSave] = useState<SaveState>({state: 'idle'});
  const [notice, setNotice] = useState<Notice | null>(null);
  const [selection, setSelection] = useState('');
  const [preferredFormat, setPreferredFormat] = useState<PostFormat>('md');
  const [compileStatus, setCompileStatus] = useState<CompileStatus>({state: 'compiling'});
  const [prOpen, setPrOpen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const docRef = useRef(doc);
  docRef.current = doc;
  const draftsRef = useRef(drafts);
  draftsRef.current = drafts;
  const lastSaved = useRef(new Map<string, string>());
  /** Draft keys being discarded: never saved again meanwhile. */
  const discarding = useRef(new Set<string>());
  const timer = useRef<number | null>(null);

  // ---------------------------------------------------------------- saving

  const needsSave = useCallback((current: EditorDoc, force: boolean): boolean => {
    if (
      discarding.current.has(current.key) ||
      lastSaved.current.get(current.key) === signature(current)
    ) {
      return false;
    }
    const hasDraft = draftsRef.current.some((draft) => draft.key === current.key);
    return force || hasDraft || current.content !== current.baseline;
  }, []);

  const saveNow = useCallback(
    async (force = false): Promise<void> => {
      if (timer.current !== null) {
        window.clearTimeout(timer.current);
        timer.current = null;
      }
      const current = docRef.current;
      if (!current || !needsSave(current, force)) {
        return;
      }
      const sig = signature(current);
      setSave({state: 'saving'});
      try {
        const draft = await store.saveDraft(current.key, {
          content: current.content,
          format: current.format,
        });
        lastSaved.current.set(current.key, sig);
        setDrafts((list) => [draft, ...list.filter((item) => item.key !== draft.key)]);
        if (docRef.current?.key === current.key) {
          setSave(
            signature(docRef.current) === sig ? {state: 'saved', at: draft.updatedAt} : {state: 'pending'},
          );
        }
      } catch (error) {
        if (docRef.current?.key === current.key) {
          setSave({state: 'error', message: errorText(error)});
        }
      }
    },
    [needsSave, store],
  );

  // Autosave a moment after the last change.
  useEffect(() => {
    if (!doc || !needsSave(doc, false)) {
      return undefined;
    }
    setSave({state: 'pending'});
    timer.current = window.setTimeout(() => {
      timer.current = null;
      void saveNow();
    }, AUTOSAVE_MS);
    return () => {
      if (timer.current !== null) {
        window.clearTimeout(timer.current);
        timer.current = null;
      }
    };
  }, [doc, needsSave, saveNow]);

  // Save right away when the tab is hidden, the page is closed, or the
  // editor is left for another page of the site.
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') {
        void saveNow();
      }
    };
    const onPageHide = () => void saveNow();
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('pagehide', onPageHide);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', onPageHide);
      void saveNow();
    };
  }, [saveNow]);

  // ------------------------------------------------------------- opening

  const load = useCallback(
    (next: EditorDoc, message: Notice | null, savedAt: string | null) => {
      setDoc(next);
      setNotice(message);
      setPrOpen(false);
      setSave(savedAt ? {state: 'saved', at: savedAt} : {state: 'idle'});
      setSelection('');
    },
    [],
  );

  const startNewPost = useCallback(
    async (format: PostFormat, message: Notice | null = null) => {
      await saveNow();
      const content = newPostTemplate({title: NEW_POST_TITLE, format, date: new Date()});
      load(
        {
          key: `${NEW_DRAFT_PREFIX}${newDraftId()}`,
          kind: 'new',
          repoPath: null,
          published: null,
          baseline: content,
          content,
          format,
        },
        message,
        null,
      );
    },
    [load, saveNow],
  );

  const openDraft = useCallback(
    async (draft: Draft, message: Notice | null = null) => {
      await saveNow();
      const repoPath = draft.key.startsWith(POST_DRAFT_PREFIX)
        ? draft.key.slice(POST_DRAFT_PREFIX.length)
        : null;
      const source = repoPath ? sources.find((item) => item.repoPath === repoPath) : undefined;
      lastSaved.current.set(draft.key, signature(draft));
      load(
        {
          key: draft.key,
          kind: repoPath ? 'existing' : 'new',
          repoPath,
          published: source?.content ?? null,
          baseline: draft.content,
          content: draft.content,
          format: repoPath ? formatOfPath(repoPath, draft.format) : draft.format,
        },
        message,
        draft.updatedAt,
      );
    },
    [load, saveNow, sources],
  );

  const openPublished = useCallback(
    async (source: BlogSource) => {
      await saveNow();
      const key = `${POST_DRAFT_PREFIX}${source.repoPath}`;
      const draft = draftsRef.current.find((item) => item.key === key);
      if (draft && draft.content !== source.content) {
        await openDraft(draft, {
          text: `This is your draft of "${source.title}", saved ${formatTime(draft.updatedAt)} in this browser.`,
          action: {
            label: 'Discard the draft and open the published version',
            run: () => void discardDraftAndOpen(source),
          },
        });
        return;
      }
      load(
        {
          key,
          kind: 'existing',
          repoPath: source.repoPath,
          published: source.content,
          baseline: source.content,
          content: source.content,
          format: source.format,
        },
        null,
        draft?.updatedAt ?? null,
      );
    },
    // discardDraftAndOpen (below) only uses refs, state setters and the store.
    [load, openDraft, saveNow],
  );

  /** Deletes a draft; false (and a message) when the store refused. */
  async function deleteDraft(key: string): Promise<boolean> {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    discarding.current.add(key);
    try {
      await store.deleteDraft(key);
    } catch (error) {
      discarding.current.delete(key);
      setSave({state: 'error', message: `the draft could not be deleted (${errorText(error)})`});
      return false;
    }
    lastSaved.current.delete(key);
    draftsRef.current = draftsRef.current.filter((item) => item.key !== key);
    setDrafts(draftsRef.current);
    return true;
  }

  async function discardDraftAndOpen(source: BlogSource): Promise<void> {
    const key = `${POST_DRAFT_PREFIX}${source.repoPath}`;
    if (!window.confirm(`Discard your draft of "${source.title}"? This cannot be undone.`)) {
      return;
    }
    if (!(await deleteDraft(key))) {
      return;
    }
    docRef.current = null;
    discarding.current.delete(key);
    load(
      {
        key,
        kind: 'existing',
        repoPath: source.repoPath,
        published: source.content,
        baseline: source.content,
        content: source.content,
        format: source.format,
      },
      null,
      null,
    );
  }

  // First load: reopen the latest draft, or start a new post.
  const started = useRef(false);
  useEffect(() => {
    if (started.current) {
      return;
    }
    started.current = true;
    void (async () => {
      let list: Draft[] = [];
      try {
        list = (await store.listDrafts()).filter(isEditorDraft);
      } catch (error) {
        setLoadError(`Your drafts could not be read: ${errorText(error)}`);
      }
      draftsRef.current = list;
      setDrafts(list);
      const latest = list[0];
      if (latest) {
        await openDraft(latest, {
          text: `Reopened your latest draft, saved ${formatTime(latest.updatedAt)}. Choose another post or draft above, or start a new post.`,
        });
      } else {
        await startNewPost('md');
      }
    })();
  }, [openDraft, startNewPost, store]);

  // ------------------------------------------------------------- actions

  async function discardDraft(): Promise<void> {
    const current = docRef.current;
    if (!current) {
      return;
    }
    if (!window.confirm('Discard this draft? This cannot be undone.')) {
      return;
    }
    if (!(await deleteDraft(current.key))) {
      return;
    }
    docRef.current = null;
    discarding.current.delete(current.key);
    if (current.kind === 'existing' && current.published !== null) {
      load({...current, baseline: current.published, content: current.published}, null, null);
    } else {
      // Nothing to go back to: start over from the template.
      await startNewPost(current.format, {text: 'The draft was discarded.'});
    }
  }

  function openSelection(): void {
    if (selection.startsWith('draft:')) {
      const draft = drafts.find((item) => item.key === selection.slice('draft:'.length));
      if (draft) {
        void openDraft(draft);
      }
    } else if (selection.startsWith('post:')) {
      const source = sources.find((item) => item.repoPath === selection.slice('post:'.length));
      if (source) {
        void openPublished(source);
      }
    }
  }

  function changeFormat(format: PostFormat): void {
    setPreferredFormat(format);
    if (!doc || doc.kind !== 'new' || doc.format === format) {
      return;
    }
    setDoc({...doc, format, content: convertTruncateMarker(doc.content, format)});
  }

  function download(fileName: string, content: string): void {
    const blob = new Blob([content], {type: 'text/markdown;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    link.rel = 'noopener';
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function onSourceKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    // Ctrl+S / Cmd+S saves the draft now instead of opening the browser's save dialog.
    if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 's') {
      event.preventDefault();
      void saveNow(true);
    }
  }

  // --------------------------------------------------------------- derived

  const split = useMemo(() => splitFrontMatter(doc?.content ?? ''), [doc?.content]);
  const {minutes, words} = useMemo(() => readingTime(doc?.content ?? ''), [doc?.content]);

  if (!doc) {
    return (
      <div className={styles.loadingEditor} role="status">
        {loadError ?? 'Opening the editor…'}
      </div>
    );
  }

  const title = split.frontMatter?.title ?? null;
  const fileName =
    doc.kind === 'existing' && doc.repoPath
      ? baseName(doc.repoPath)
      : safeFileName(title, split.frontMatter?.date ?? null, doc.format);
  const repoPath = doc.kind === 'existing' && doc.repoPath ? doc.repoPath : `${BLOG_DIR}/${fileName}`;
  const clash =
    doc.kind === 'new'
      ? sources.find((source) => stripExtension(source.repoPath) === stripExtension(repoPath))
      : undefined;
  const hasDraft = drafts.some((draft) => draft.key === doc.key);
  const hasToken = typeof token === 'string' && token.trim() !== '';

  const blockers: string[] = [];
  if (split.problem) {
    blockers.push(split.problem);
  }
  if (!title || (doc.kind === 'new' && title === NEW_POST_TITLE)) {
    blockers.push('Give the post a title in the front matter (for a new post it also names the file).');
  }
  if (clash) {
    blockers.push(
      `A published post already uses the file name ${baseName(clash.repoPath)}. Change the title or the date.`,
    );
  }
  if (compileStatus.state === 'error') {
    blockers.push('Fix the error shown in the preview.');
  }
  if (!doc.content.trim()) {
    blockers.push('The post is empty.');
  }
  if (doc.kind === 'existing' && doc.published !== null && doc.content === doc.published) {
    blockers.push('There are no changes to the published post yet.');
  }
  if (!repo) {
    blockers.push('The site configuration does not name the repository (customFields.repo).');
  }

  const editorDrafts = drafts.filter(isEditorDraft);

  return (
    <div className={styles.editor}>
      <div className={styles.toolbar}>
        <div className={styles.toolbarGroup}>
          <label className={styles.field} htmlFor={`${id}-open`}>
            <span className={styles.fieldLabel}>Open a post or a draft</span>
            <select
              id={`${id}-open`}
              className={styles.input}
              value={selection}
              onChange={(event) => setSelection(event.target.value)}>
              <option value="">Choose…</option>
              {editorDrafts.length > 0 && (
                <optgroup label="Your drafts (this browser)">
                  {editorDrafts.map((draft) => (
                    <option key={draft.key} value={`draft:${draft.key}`}>
                      {draftLabel(draft)}
                    </option>
                  ))}
                </optgroup>
              )}
              <optgroup label={sources.length > 0 ? 'Published posts' : 'Published posts (none found)'}>
                {sources.map((source) => (
                  <option key={source.repoPath} value={`post:${source.repoPath}`}>
                    {source.title} ({baseName(source.repoPath)})
                  </option>
                ))}
              </optgroup>
            </select>
          </label>
          <button
            type="button"
            className="button button--secondary"
            onClick={openSelection}
            disabled={!selection}>
            Open
          </button>
          <button
            type="button"
            className="button button--secondary"
            onClick={() => void startNewPost(preferredFormat)}>
            New post
          </button>
        </div>

        <fieldset className={styles.formatToggle}>
          <legend className={styles.fieldLabel}>Format</legend>
          <div className={styles.segmented}>
            {(['md', 'mdx'] as const).map((format) => (
              <label
                key={format}
                className={clsx(
                  styles.segment,
                  doc.format === format && styles.segmentActive,
                  doc.kind === 'existing' && styles.segmentDisabled,
                )}>
                <input
                  type="radio"
                  className="rid-sr-only"
                  name={`${id}-format`}
                  value={format}
                  checked={doc.format === format}
                  disabled={doc.kind === 'existing'}
                  onChange={() => changeFormat(format)}
                />
                {format}
              </label>
            ))}
          </div>
          <span className={styles.fieldHint}>
            {doc.kind === 'existing'
              ? 'Set by the file extension of the published post.'
              : doc.format === 'mdx'
                ? 'MDX: Markdown plus JSX and the site components.'
                : 'Markdown (CommonMark), like the docs.'}
          </span>
        </fieldset>

        <div className={styles.toolbarActions}>
          <button
            type="button"
            className="button button--secondary"
            onClick={() => download(fileName, doc.content)}>
            Download
          </button>
          {hasToken ? (
            <button
              type="button"
              className="button button--primary"
              aria-expanded={prOpen}
              onClick={() => {
                void saveNow();
                setPrOpen((open) => !open);
              }}>
              Open pull request
            </button>
          ) : null}
        </div>
      </div>

      <div className={styles.statusRow}>
        <p className={styles.fileName}>
          <span className={styles.fieldLabel}>File</span> <code>{repoPath}</code>
          {doc.kind === 'existing' ? (
            <span className="badge badge--info">published post</span>
          ) : (
            <span className="badge badge--secondary">new post</span>
          )}
        </p>
        <p className={styles.saveStatus} role="status" aria-live="polite">
          {saveText(save, hasDraft, backend === 'memory')}
        </p>
        {hasDraft && (
          <button
            type="button"
            className="button button--sm button--link"
            onClick={() => void discardDraft()}>
            Discard draft
          </button>
        )}
      </div>

      {loadError && (
        <div className="alert alert--warning" role="alert">
          {loadError}
        </div>
      )}

      {notice && (
        <div className={clsx('alert alert--info', styles.notice)} role="status">
          <span>{notice.text}</span>
          <span className={styles.noticeActions}>
            {notice.action && (
              <button
                type="button"
                className="button button--sm button--secondary"
                onClick={notice.action.run}>
                {notice.action.label}
              </button>
            )}
            <button
              type="button"
              className="button button--sm button--link"
              onClick={() => setNotice(null)}>
              Dismiss
            </button>
          </span>
        </div>
      )}

      {!hasToken && (
        <div className={clsx('alert alert--secondary', styles.tokenPrompt)} role="note">
          <strong>Want to open a pull request from here?</strong> Add a GitHub token in the GitHub
          menu at the right of the navigation bar (<em>Sign in</em>, or your username when signed
          in). The token is kept on this device only and is sent only to api.github.com. Without a
          token, use <strong>Download</strong> and add the file to <code>{BLOG_DIR}/</code> in a
          pull request yourself.
        </div>
      )}

      {prOpen && hasToken && repo && (
        <PullRequestPanel
          token={token as string}
          repo={repo}
          path={repoPath}
          content={doc.content}
          postTitle={title ?? NEW_POST_TITLE}
          isNewPost={doc.kind === 'new'}
          blockers={blockers}
          onClose={() => setPrOpen(false)}
        />
      )}

      <div className={styles.split}>
        <section className={styles.pane} aria-labelledby={`${id}-source-heading`}>
          <div className={styles.paneHeader}>
            <h2 id={`${id}-source-heading`} className={styles.paneTitle}>
              <label htmlFor={`${id}-source`}>Source ({doc.format === 'mdx' ? 'MDX' : 'Markdown'})</label>
            </h2>
            <span className={styles.paneMeta}>
              {words} words · {minutes} min read
            </span>
          </div>
          <textarea
            id={`${id}-source`}
            className={styles.source}
            value={doc.content}
            onChange={(event) => {
              const content = event.target.value;
              setDoc((current) => (current ? {...current, content} : current));
            }}
            onKeyDown={onSourceKeyDown}
            spellCheck
            autoCapitalize="sentences"
            autoComplete="off"
            aria-describedby={`${id}-source-help`}
          />
          <p id={`${id}-source-help`} className={styles.paneFooter}>
            Front matter at the top (title, description, tags, date). The text above the{' '}
            <code>{doc.format === 'mdx' ? '{/* truncate */}' : '<!-- truncate -->'}</code> line is the
            excerpt on the blog list. Ctrl+S (⌘S) saves the draft now.
          </p>
        </section>

        <section className={clsx(styles.pane, styles.previewPane)} aria-labelledby={`${id}-preview-heading`}>
          <div className={styles.paneHeader}>
            <h2 id={`${id}-preview-heading`} className={styles.paneTitle}>
              Preview
            </h2>
            <span className={styles.paneMeta} aria-live="polite">
              {compileStatus.state === 'compiling'
                ? 'Updating…'
                : compileStatus.state === 'error'
                  ? 'Error'
                  : 'Up to date'}
            </span>
          </div>
          <div className={styles.previewScroll}>
            <Preview
              body={split.body}
              format={doc.format}
              frontMatter={split.frontMatter}
              readingMinutes={minutes}
              onStatus={setCompileStatus}
            />
          </div>
          <p className={styles.paneFooter}>
            {doc.format === 'mdx'
              ? 'MDX runs JavaScript in this page: only preview MDX you trust. '
              : ''}
            The preview uses CommonMark{doc.format === 'mdx' ? ' and MDX' : ''} with the site&apos;s
            components; tables, admonitions (<code>:::note</code>)
            {doc.format === 'md' ? ', imports and HTML tags' : ' and imports'} are rendered only in
            the published build.
          </p>
        </section>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers

function signature(value: {content: string; format: PostFormat}): string {
  return `${value.format}\u0000${value.content}`;
}

function isEditorDraft(draft: Draft): boolean {
  return draft.key.startsWith(POST_DRAFT_PREFIX) || draft.key.startsWith(NEW_DRAFT_PREFIX);
}

function newDraftId(): string {
  const random = Math.random().toString(36).slice(2, 8);
  return `${Date.now().toString(36)}-${random}`;
}

function draftLabel(draft: Draft): string {
  const title = splitFrontMatter(draft.content).frontMatter?.title ?? NEW_POST_TITLE;
  const what = draft.key.startsWith(POST_DRAFT_PREFIX)
    ? `draft of ${baseName(draft.key.slice(POST_DRAFT_PREFIX.length))}`
    : 'new post';
  return `${title} (${what}, ${formatTime(draft.updatedAt)})`;
}

function saveText(save: SaveState, hasDraft: boolean, memoryOnly: boolean): string {
  // With the in-memory store (storage blocked, e.g. some private windows) a
  // draft lasts only until the tab is closed; say so instead of "saved".
  const where = memoryOnly ? 'for this tab only (this browser blocks storage)' : 'in this browser';
  switch (save.state) {
    case 'pending':
      return 'Unsaved changes…';
    case 'saving':
      return 'Saving the draft…';
    case 'saved':
      return `Draft saved ${where}, ${formatTime(save.at)}`;
    case 'error':
      return `The draft could not be saved: ${save.message}`;
    default:
      return hasDraft ? `Draft saved ${where}` : `No changes yet. Drafts are saved ${where}.`;
  }
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : timeFormat.format(date);
}

function errorText(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'unknown error';
}

function baseName(path: string): string {
  return path.split('/').pop() ?? path;
}

function stripExtension(path: string): string {
  return path.replace(/\.mdx?$/i, '').toLowerCase();
}

function formatOfPath(path: string, fallback: PostFormat): PostFormat {
  if (/\.mdx$/i.test(path)) {
    return 'mdx';
  }
  return /\.md$/i.test(path) ? 'md' : fallback;
}

/** 'YYYY-MM-DD-<slug>.<format>' from the front matter, with today when the date is missing. */
function safeFileName(title: string | null, date: string | null, format: PostFormat): string {
  try {
    return postFileName({title: title ?? NEW_POST_TITLE, format, date: date ?? new Date()});
  } catch {
    return postFileName({title: title ?? NEW_POST_TITLE, format, date: new Date()});
  }
}

function readRepo(value: unknown): RepoConfig | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const {owner, name, branch} = value as Record<string, unknown>;
  if (typeof owner !== 'string' || typeof name !== 'string' || !owner || !name) {
    return null;
  }
  return {owner, name, branch: typeof branch === 'string' && branch ? branch : 'main'};
}

function readBlogSources(value: unknown): BlogSource[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const result: BlogSource[] = [];
  for (const item of value as unknown[]) {
    if (!item || typeof item !== 'object') {
      continue;
    }
    const {repoPath, file, title, format, content} = item as Record<string, unknown>;
    if (typeof repoPath !== 'string' || typeof content !== 'string' || !/\.mdx?$/i.test(repoPath)) {
      continue;
    }
    const resolvedFormat: PostFormat =
      format === 'mdx' || format === 'md' ? format : formatOfPath(repoPath, 'md');
    result.push({
      repoPath,
      file: typeof file === 'string' ? file : baseName(repoPath),
      title:
        typeof title === 'string' && title.trim()
          ? title.trim()
          : (splitFrontMatter(content).frontMatter?.title ?? baseName(repoPath)),
      format: resolvedFormat,
      content,
    });
  }
  return result.sort((a, b) => b.repoPath.localeCompare(a.repoPath));
}
