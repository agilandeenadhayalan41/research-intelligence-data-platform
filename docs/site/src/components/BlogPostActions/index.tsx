// Edit and Delete for one blog post, next to its title, in the post list and
// on the post's page.
//
// - Edit opens the editor on the post: /blog/editor?post=<repo path>.
// - Delete opens a dialog that opens a pull request deleting the file (with a
//   GitHub token), or explains how to add a token. The post leaves the site
//   only when that pull request is merged.

import React, {useCallback, useRef, useState, type ReactNode} from 'react';
import Link from '@docusaurus/Link';
import {isBlogPostPath} from '@site/src/lib/github.mjs';
import {blogEditorUrl} from '@site/src/lib/blogPosts.mjs';

import DeletePostDialog from './DeletePostDialog';
import styles from './styles.module.css';

type Props = {
  /** Repository path of the post, from repoPathForDoc(metadata.source). */
  repoPath: string | null;
  /** Title of the post. */
  title: string;
};

function PencilIcon(): ReactNode {
  return (
    <svg className={styles.icon} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path
        d="M10.5 2.5l3 3L6 13H3v-3l7.5-7.5z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function TrashIcon(): ReactNode {
  return (
    <svg className={styles.icon} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path
        d="M2.75 4.25h10.5M6.25 4.25V2.75h3.5v1.5M4.25 4.25l.6 9h6.3l.6-9M6.75 6.75v4M9.25 6.75v4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Renders nothing when the post has no blog post path in the repository. */
export default function BlogPostActions({repoPath, title}: Props): ReactNode {
  const [open, setOpen] = useState(false);
  const deleteButton = useRef<HTMLButtonElement>(null);

  const close = useCallback(() => {
    setOpen(false);
    // Back to the button that opened the dialog.
    window.requestAnimationFrame(() => deleteButton.current?.focus());
  }, []);

  if (!isBlogPostPath(repoPath)) {
    return null;
  }

  return (
    <div className={styles.actions}>
      <Link className="button button--sm button--secondary" to={blogEditorUrl(repoPath)}>
        <PencilIcon />
        Edit<span className="rid-sr-only"> &ldquo;{title}&rdquo;</span>
      </Link>
      <button
        ref={deleteButton}
        type="button"
        className="button button--sm button--outline button--danger"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}>
        <TrashIcon />
        Delete<span className="rid-sr-only"> &ldquo;{title}&rdquo;</span>
      </button>
      {open && <DeletePostDialog repoPath={repoPath} title={title} onClose={close} />}
    </div>
  );
}
