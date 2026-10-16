// Likes and comments at the end of a doc or blog post.
//
// Guest first: the like button and the comment form work at once, without
// an account (comments are signed "Guest"). A quiet line under the form
// offers "Sign in with GitHub" (a plain link to /api/auth/login that comes
// back to this page) for visitors who want their GitHub name on what they
// post; signed in, comments carry the verified avatar and login. Nothing here
// ever blocks reading or guest writes.
//
// Everything goes through the CommunityStore (useCommunityStore), which keeps
// the data in this browser only (IndexedDB, else localStorage, else memory).
// Other visitors do not see it, and the section says so.

import React, {useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent} from 'react';
import clsx from 'clsx';
import Heading from '@theme/Heading';
import useIsBrowser from '@docusaurus/useIsBrowser';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import {useGitHubUser, useSignInClick, useSignInHref} from '@site/src/lib/GitHubUserContext';
import {useCommunityBackend, useCommunityStore, type CommunityBackend} from '@site/src/lib/CommunityStoreContext';
import {
  canDeleteComment,
  COMMENT_LENGTH_ERROR,
  COMMENT_MAX_LENGTH,
  type Comment,
  type Likes,
} from '@site/src/lib/storage.mjs';
import Avatar from '../GitHubUserNavbarItem/Avatar';
import {formatDateTime, plural, profileUrl} from '../PageMetaBar/format';
import styles from './styles.module.css';

type Props = {
  /** Stable id of the page: its repository path (or its permalink). */
  pageId: string;
  /** Id of the comments heading. */
  id?: string;
};

const NUMBER = new Intl.NumberFormat('en');

function storageNote(backend: CommunityBackend | null): string {
  if (backend === 'memory') {
    return 'This browser blocks site storage, so likes and comments last only until you leave the page. Other visitors do not see them.';
  }
  return 'Saved in this browser only. Other visitors do not see your likes and comments.';
}

/** Characters as people count them (an emoji is one). */
function length(text: string): number {
  return Array.from(text.trim()).length;
}

function HeartIcon({filled}: {filled: boolean}): React.JSX.Element {
  return (
    <svg className={styles.heart} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path
        d="M8 13.75S1.75 10.1 1.75 5.9A3.15 3.15 0 015 2.75c1.3 0 2.4.7 3 1.8.6-1.1 1.7-1.8 3-1.8a3.15 3.15 0 013.25 3.15C14.25 10.1 8 13.75 8 13.75z"
        fill={filled ? 'currentColor' : 'none'}
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CommentItem({
  comment,
  canDelete,
  onDelete,
  locale,
}: {
  comment: Comment;
  canDelete: boolean;
  onDelete: (comment: Comment) => void;
  locale: string;
}): React.JSX.Element {
  const {author} = comment;
  const when = formatDateTime(comment.createdAt, locale);
  return (
    <li className={styles.comment}>
      <Avatar src={author.avatarUrl} name={author.login ?? author.name} size="md" />
      <div className={styles.commentMain}>
        <div className={styles.commentMeta}>
          {author.login ? (
            <a
              href={profileUrl(author.login)}
              target="_blank"
              rel="noopener noreferrer"
              className={styles.commentAuthor}>
              {author.name}
            </a>
          ) : (
            <span className={styles.commentAuthor}>{author.name}</span>
          )}
          {author.login && author.login !== author.name && (
            <span className={styles.commentLogin}>@{author.login}</span>
          )}
          {when && (
            <time className={styles.commentDate} dateTime={comment.createdAt} title={comment.createdAt}>
              {when}
            </time>
          )}
          {canDelete && (
            <button
              type="button"
              className={styles.deleteButton}
              onClick={() => onDelete(comment)}
              aria-label={`Delete your comment${when ? ` from ${when}` : ''}`}>
              Delete
            </button>
          )}
        </div>
        <p className={styles.commentBody}>{comment.body}</p>
      </div>
    </li>
  );
}

function PageCommunityContent({pageId, id = 'page-comments'}: Props): React.JSX.Element {
  const isBrowser = useIsBrowser();
  const {i18n} = useDocusaurusContext();
  const store = useCommunityStore();
  const backend = useCommunityBackend();
  const {user, userKey, status, verified, availability} = useGitHubUser();
  const signInHref = useSignInHref();
  const onSignInClick = useSignInClick();

  const [likes, setLikes] = useState<Likes | null>(null);
  const [comments, setComments] = useState<Comment[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [liking, setLiking] = useState(false);
  const [text, setText] = useState('');
  const [posting, setPosting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const textarea = useRef<HTMLTextAreaElement>(null);

  const textareaId = useId();
  const hintId = useId();
  const errorId = useId();

  useEffect(() => {
    let active = true;
    Promise.allSettled([store.getLikes(pageId), store.listComments(pageId)]).then(
      ([likesResult, commentsResult]) => {
        if (!active) {
          return;
        }
        if (likesResult.status === 'fulfilled') {
          setLikes(likesResult.value);
        }
        if (commentsResult.status === 'fulfilled') {
          setComments(commentsResult.value);
        }
        if (likesResult.status === 'rejected' || commentsResult.status === 'rejected') {
          setLoadError('Could not read the likes and comments saved in this browser.');
        }
      },
    );
    return () => {
      active = false;
    };
  }, [store, pageId]);

  const liked = likes ? likes.likedBy.includes(userKey) : false;
  const ready = isBrowser && status !== 'loading';

  // The buttons stay enabled while the store works (a disabled focused button
  // drops the keyboard focus to the page); the handlers ignore repeats.
  const toggleLike = async () => {
    if (liking) {
      return;
    }
    setLiking(true);
    try {
      const next = await store.toggleLike(pageId, userKey);
      setLikes(next);
      setAnnouncement(next.likedBy.includes(userKey) ? 'You like this page.' : 'Like removed.');
    } catch {
      setAnnouncement('Could not save the like in this browser.');
    } finally {
      setLiking(false);
    }
  };

  const count = length(text);
  const tooLong = count > COMMENT_MAX_LENGTH;

  const post = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    if (posting) {
      return;
    }
    if (count < 1 || tooLong) {
      setFormError(
        count < 1
          ? 'Write a comment first.'
          : `A comment can be at most ${NUMBER.format(COMMENT_MAX_LENGTH)} characters.`,
      );
      textarea.current?.focus();
      return;
    }
    setPosting(true);
    setFormError(null);
    try {
      const author = user
        ? {login: user.login, name: user.name, avatarUrl: user.avatarUrl}
        : {login: null, name: 'Guest', avatarUrl: null};
      const comment = await store.addComment(pageId, {author, body: text});
      setComments((current) => [...(current ?? []), comment]);
      setText('');
      // The cleared form disables "Post comment": keep the focus in the form.
      textarea.current?.focus();
      setAnnouncement('Comment posted.');
    } catch (error) {
      setFormError(
        error instanceof Error && error.message === COMMENT_LENGTH_ERROR
          ? `A comment must be 1 to ${NUMBER.format(COMMENT_MAX_LENGTH)} characters.`
          : 'Could not save the comment in this browser.',
      );
    } finally {
      setPosting(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      void post();
    }
  };

  const remove = async (comment: Comment) => {
    if (!window.confirm('Delete this comment? This cannot be undone.')) {
      return;
    }
    try {
      const deleted = await store.deleteComment(pageId, comment.id, userKey);
      if (deleted) {
        setComments((current) => (current ?? []).filter((item) => item.id !== comment.id));
        setAnnouncement('Comment deleted.');
      } else {
        setAnnouncement('That comment could not be deleted.');
      }
    } catch {
      setAnnouncement('Could not delete the comment in this browser.');
    }
  };

  const likeCount = likes?.count ?? null;

  return (
    <section className={styles.community} aria-labelledby={id}>
      <div className={styles.likeRow}>
        <button
          type="button"
          className={clsx(styles.likeButton, liked && styles.liked)}
          aria-pressed={liked}
          onClick={toggleLike}
          disabled={!ready || likes === null}
          aria-busy={liking || undefined}>
          <HeartIcon filled={liked} />
          <span>Like</span>
        </button>
        <span className={styles.likeCount}>
          {likeCount === null ? '' : plural(likeCount, 'like', 'likes')}
        </span>
        <span className={clsx('rid-muted-note', styles.note)}>{storageNote(backend)}</span>
      </div>

      <Heading as="h2" id={id} className={styles.title}>
        {comments && comments.length > 0 ? `Comments (${comments.length})` : 'Comments'}
      </Heading>

      {loadError && (
        <p className={styles.error} role="alert">
          {loadError}
        </p>
      )}

      {comments === null ? (
        !loadError && <p className={styles.empty}>{isBrowser ? 'Loading comments…' : 'Comments load in your browser.'}</p>
      ) : comments.length === 0 ? (
        <p className={styles.empty}>No comments yet.</p>
      ) : (
        <ol className={styles.comments}>
          {comments.map((comment) => (
            <CommentItem
              key={comment.id}
              comment={comment}
              canDelete={canDeleteComment(comment, userKey)}
              onDelete={remove}
              locale={i18n.currentLocale}
            />
          ))}
        </ol>
      )}

      <form className={styles.form} onSubmit={post} noValidate>
        <label htmlFor={textareaId} className={styles.label}>
          Add a comment
        </label>
        <textarea
          ref={textarea}
          id={textareaId}
          className={styles.textarea}
          rows={4}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            if (formError) {
              setFormError(null);
            }
          }}
          onKeyDown={onKeyDown}
          disabled={!ready}
          aria-invalid={formError || tooLong ? true : undefined}
          aria-describedby={clsx(hintId, formError && errorId)}
        />
        <div className={styles.formRow}>
          <p id={hintId} className={clsx('rid-muted-note', styles.hint)}>
            {user ? (
              verified ? (
                <>
                  Commenting as <strong>@{user.login}</strong>.
                </>
              ) : (
                <>
                  Commenting as <strong>@{user.login}</strong> (not verified on this deployment).
                </>
              )
            ) : availability === 'not-configured' ? (
              <>Commenting as a guest.</>
            ) : (
              <>
                Commenting as a guest ·{' '}
                <a href={signInHref} onClick={onSignInClick}>
                  Sign in with GitHub
                </a>{' '}
                to show your name.
              </>
            )}{' '}
            <span className={tooLong ? styles.overLimit : undefined}>
              {NUMBER.format(count)} / {NUMBER.format(COMMENT_MAX_LENGTH)}
            </span>
          </p>
          <button
            type="submit"
            className="button button--primary button--sm"
            disabled={!ready || count < 1 || tooLong}
            aria-busy={posting || undefined}>
            {posting ? 'Posting…' : 'Post comment'}
          </button>
        </div>
        {formError && (
          <p id={errorId} className={styles.error} role="alert">
            {formError}
          </p>
        )}
      </form>

      <p className="rid-sr-only" aria-live="polite">
        {announcement}
      </p>
    </section>
  );
}

/** Likes and comments of the page `pageId`, saved in this browser. */
export default function PageCommunity(props: Props): React.JSX.Element {
  // Another page starts with fresh state.
  return <PageCommunityContent key={props.pageId} {...props} />;
}
