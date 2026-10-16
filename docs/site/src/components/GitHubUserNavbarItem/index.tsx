// Navbar item `{type: 'custom-githubUser'}` (registered in
// src/theme/NavbarItem/ComponentTypes.tsx).
//
// Desktop navbar: a "Sign in" button, or the avatar and username once signed
// in; it opens a small popover with the account panel.
// Mobile sidebar (`mobile` prop): the same button as a menu entry; the panel
// opens inline below it.

import React, {useCallback, useEffect, useId, useRef, useState, type KeyboardEvent} from 'react';
import clsx from 'clsx';
import {useGitHubUser} from '@site/src/lib/GitHubUserContext';
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

function TriggerContent(): React.JSX.Element {
  const {user} = useGitHubUser();
  if (!user) {
    return <span>Sign in</span>;
  }
  return (
    <>
      <Avatar src={user.avatarUrl} name={user.name} size="sm" />
      <span className={styles.triggerLogin}>{user.login}</span>
      <span className="rid-sr-only">: account menu</span>
    </>
  );
}

function useAccountLabel(): string {
  const {user} = useGitHubUser();
  return user ? `GitHub account ${user.login}` : 'Sign in with GitHub';
}

function DesktopItem({className}: {className?: string}): React.JSX.Element {
  const {user, status} = useGitHubUser();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const label = useAccountLabel();

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

  return (
    <div
      ref={root}
      className={clsx('navbar__item', styles.item, status === 'loading' && styles.loading, className)}
      onKeyDown={onKeyDown}
      onBlur={onBlur}>
      <button
        ref={trigger}
        type="button"
        className={clsx(
          styles.trigger,
          user ? styles.triggerUser : 'button button--sm button--outline button--primary',
        )}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-haspopup="dialog"
        title={label}
        onClick={() => setOpen((value) => !value)}>
        <TriggerContent />
      </button>
      {open && (
        <div id={panelId} className={styles.popover} role="dialog" aria-label={label} tabIndex={-1}>
          <AccountPanel autoFocus />
        </div>
      )}
    </div>
  );
}

function MobileItem({className}: {className?: string}): React.JSX.Element {
  const {user, status} = useGitHubUser();
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const label = useAccountLabel();

  return (
    <li className={clsx('menu__list-item', status === 'loading' && styles.loading, className)}>
      <button
        type="button"
        className={clsx('menu__link', styles.mobileTrigger, open && styles.mobileTriggerOpen)}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        title={label}
        onClick={() => setOpen((value) => !value)}>
        {user ? <TriggerContent /> : <span>Sign in with GitHub</span>}
      </button>
      {open && (
        <div id={panelId} className={styles.mobilePanel} role="group" aria-label={label}>
          <AccountPanel />
        </div>
      )}
    </li>
  );
}

export default function GitHubUserNavbarItem({mobile = false, className}: Props): React.JSX.Element {
  return mobile ? <MobileItem className={className} /> : <DesktopItem className={className} />;
}
