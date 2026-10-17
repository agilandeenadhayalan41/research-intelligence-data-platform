// Navbar item `{type: 'custom-githubUser'}` (registered in
// src/theme/NavbarItem/ComponentTypes.tsx).
//
// Signed out: a small "Sign in" button that goes to GitHub (a plain link to
// /api/auth/login; nothing opens on its own), and next to it a small menu
// button for the account switcher, the GitHub token and, without the auth
// functions, the unverified username fallback. When the deployment has no
// OAuth settings, sign-in is hidden and only the "GitHub token" menu stays
// (with the token and the username fallback).
// Signed in: the avatar and username, which open the account menu (profile
// link, "Switch account", "Sign out", other accounts, GitHub token).
// Mobile sidebar (`mobile` prop): the same entries as menu links; the menu
// opens inline below them.
//
// Other components can open the menu with useOpenAccountMenu() (it opens with
// the token field shown): the item that is on screen (the navbar one on a wide
// screen, the sidebar one on a narrow one) reacts to the request.

import React, {useCallback, useEffect, useId, useRef, useState, type KeyboardEvent} from 'react';
import clsx from 'clsx';
import {useNavbarMobileSidebar} from '@docusaurus/theme-common/internal';
import {useGitHubUser, useSignInClick, useSignInHref} from '@site/src/lib/GitHubUserContext';
import AccountPanel from './AccountPanel';
import Avatar from './Avatar';
import styles from './styles.module.css';

type Props = {
  /** Set by the theme when the item is rendered in the mobile sidebar. */
  mobile?: boolean;
  className?: string;
  // Passed by the theme; not used by this item.
  position?: 'left' | 'right';
  onClick?: unknown;
};

function ChevronIcon(): React.JSX.Element {
  return (
    <svg className={styles.chevron} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function UserTrigger(): React.JSX.Element | null {
  const {user, verified} = useGitHubUser();
  if (!user) {
    return null;
  }
  return (
    <>
      <Avatar src={user.avatarUrl} name={user.name} size="sm" />
      <span className={styles.triggerLogin}>{user.login}</span>
      {!verified && <span className={styles.unverifiedDot} aria-hidden="true" />}
      <span className="rid-sr-only">{verified ? ': account menu' : ' (not verified): account menu'}</span>
    </>
  );
}

function useMenuLabel(): string {
  const {user, verified, availability} = useGitHubUser();
  if (user) {
    return verified ? `GitHub account ${user.login}` : `GitHub username ${user.login} (not verified on this deployment)`;
  }
  return availability === 'not-configured' ? 'GitHub token and username' : 'Sign-in options and GitHub token';
}

/**
 * Calls `onRequest` for each useOpenAccountMenu() call made while `active`
 * (requests made before the item mounted are ignored).
 */
function useAccountMenuRequest(active: boolean, onRequest: () => void): void {
  const {accountMenuRequest} = useGitHubUser();
  const handled = useRef(accountMenuRequest);
  const callback = useRef(onRequest);
  callback.current = onRequest;
  useEffect(() => {
    if (accountMenuRequest === handled.current) {
      return;
    }
    handled.current = accountMenuRequest;
    if (active) {
      callback.current();
    }
  }, [accountMenuRequest, active]);
}

function DesktopItem({className}: {className?: string}): React.JSX.Element {
  const {user, status, availability} = useGitHubUser();
  const signInHref = useSignInHref();
  const onSignInClick = useSignInClick();
  const mobileSidebar = useNavbarMobileSidebar();
  const [open, setOpen] = useState(false);
  // Opened by useOpenAccountMenu(): show the token field. `requests` remounts
  // the panel (and moves the focus into it) when it was already open.
  const [revealToken, setRevealToken] = useState(false);
  const [requests, setRequests] = useState(0);
  useAccountMenuRequest(!mobileSidebar.shouldRender, () => {
    setRevealToken(true);
    setRequests((count) => count + 1);
    setOpen(true);
  });
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const label = useMenuLabel();
  const canSignIn = availability !== 'not-configured';

  const close = useCallback((restoreFocus: boolean) => {
    setOpen(false);
    if (restoreFocus) {
      trigger.current?.focus();
    }
  }, []);

  // Close on a click or tap outside.
  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const onPointerDown = (event: PointerEvent) => {
      if (root.current && event.target instanceof Node && !root.current.contains(event.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open]);

  // Escape also closes it when the focus is not inside the item (for example
  // after it dropped to the page). Inside the item, onKeyDown below handles it.
  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const onDocumentKeyDown = (event: globalThis.KeyboardEvent) => {
      if (
        event.key === 'Escape' &&
        !event.defaultPrevented &&
        !(root.current && event.target instanceof Node && root.current.contains(event.target))
      ) {
        close(true);
      }
    };
    document.addEventListener('keydown', onDocumentKeyDown);
    return () => document.removeEventListener('keydown', onDocumentKeyDown);
  }, [open, close]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape' && open) {
      event.stopPropagation();
      close(true);
    }
  };

  // Close when the keyboard focus leaves the item.
  const onBlur = (event: React.FocusEvent<HTMLDivElement>) => {
    const next = event.relatedTarget;
    if (open && next instanceof Node && root.current && !root.current.contains(next)) {
      setOpen(false);
    }
  };

  const toggle = () => {
    setRevealToken(false);
    setOpen((value) => !value);
  };
  const controls = open ? panelId : undefined;

  let triggers: React.JSX.Element;
  if (user) {
    triggers = (
      <button
        ref={trigger}
        type="button"
        className={clsx(styles.trigger, styles.triggerUser)}
        aria-expanded={open}
        aria-controls={controls}
        aria-haspopup="dialog"
        title={label}
        onClick={toggle}>
        <UserTrigger />
      </button>
    );
  } else if (canSignIn) {
    triggers = (
      <div className={clsx('button-group', styles.signInGroup)}>
        <a
          href={signInHref}
          className={clsx('button button--sm button--outline button--primary', styles.trigger)}
          onClick={onSignInClick}
          aria-busy={status === 'signing-in' || undefined}>
          {status === 'signing-in' ? 'Signing in…' : 'Sign in'}
        </a>
        <button
          ref={trigger}
          type="button"
          className={clsx('button button--sm button--outline button--primary', styles.optionsButton)}
          aria-expanded={open}
          aria-controls={controls}
          aria-haspopup="dialog"
          aria-label={label}
          title={label}
          onClick={toggle}>
          <ChevronIcon />
        </button>
      </div>
    );
  } else {
    triggers = (
      <button
        ref={trigger}
        type="button"
        className={clsx('button button--sm button--outline button--secondary', styles.trigger)}
        aria-expanded={open}
        aria-controls={controls}
        aria-haspopup="dialog"
        title={label}
        onClick={toggle}>
        GitHub token
      </button>
    );
  }

  return (
    <div
      ref={root}
      className={clsx('navbar__item', styles.item, status === 'loading' && styles.loading, className)}
      onKeyDown={onKeyDown}
      onBlur={onBlur}>
      {triggers}
      {open && (
        <div id={panelId} className={styles.popover} role="dialog" aria-label={label} tabIndex={-1}>
          <AccountPanel key={requests} autoFocus revealToken={revealToken} />
        </div>
      )}
    </div>
  );
}

function MobileItem({className}: {className?: string}): React.JSX.Element {
  const {user, status, availability} = useGitHubUser();
  const signInHref = useSignInHref();
  const onSignInClick = useSignInClick();
  const mobileSidebar = useNavbarMobileSidebar();
  const [open, setOpen] = useState(false);
  const [revealToken, setRevealToken] = useState(false);
  const [requests, setRequests] = useState(0);
  const item = useRef<HTMLLIElement>(null);
  const panelId = useId();
  const label = useMenuLabel();
  const canSignIn = availability !== 'not-configured';
  const itemClass = clsx('menu__list-item', status === 'loading' && styles.loading, className);

  // The sidebar may still be sliding in (visibility: hidden at first), and
  // focus() does nothing on a hidden element: focus the panel once it shows.
  const [focusReady, setFocusReady] = useState(false);

  useAccountMenuRequest(mobileSidebar.shouldRender, () => {
    setRevealToken(true);
    setFocusReady(false);
    setRequests((count) => count + 1);
    setOpen(true);
  });
  useEffect(() => {
    if (requests === 0) {
      return undefined;
    }
    let frame = 0;
    let tries = 0;
    const whenVisible = () => {
      const element = item.current;
      tries += 1;
      if ((element && getComputedStyle(element).visibility === 'visible') || tries > 60) {
        // The item is the last one of the sidebar menu: bring the panel into view.
        element?.scrollIntoView?.({block: 'nearest'});
        setFocusReady(true);
        return;
      }
      frame = requestAnimationFrame(whenVisible);
    };
    frame = requestAnimationFrame(whenVisible);
    return () => cancelAnimationFrame(frame);
  }, [requests]);

  return (
    <>
      {!user && canSignIn && (
        <li className={itemClass}>
          <a className={clsx('menu__link', styles.mobileTrigger)} href={signInHref} onClick={onSignInClick}>
            {status === 'signing-in' ? 'Signing in…' : 'Sign in with GitHub'}
          </a>
        </li>
      )}
      <li ref={item} className={itemClass}>
        <button
          type="button"
          className={clsx('menu__link', styles.mobileTrigger, open && styles.mobileTriggerOpen)}
          aria-expanded={open}
          aria-controls={open ? panelId : undefined}
          title={label}
          onClick={() => {
            setRevealToken(false);
            setOpen((value) => !value);
          }}>
          {user ? <UserTrigger /> : <span>{canSignIn ? 'Accounts and GitHub token' : 'GitHub token'}</span>}
        </button>
        {open && (
          <div id={panelId} className={styles.mobilePanel} role="group" aria-label={label}>
            <AccountPanel key={requests} autoFocus={revealToken && focusReady} revealToken={revealToken} />
          </div>
        )}
      </li>
    </>
  );
}

export default function GitHubUserNavbarItem({mobile = false, className}: Props): React.JSX.Element {
  return mobile ? <MobileItem className={className} /> : <DesktopItem className={className} />;
}
