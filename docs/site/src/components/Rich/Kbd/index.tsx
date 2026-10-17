import React, {type ReactNode} from 'react';
import styles from './styles.module.css';

/** A keyboard key, for example <Kbd>Ctrl</Kbd> + <Kbd>S</Kbd>. */
export default function Kbd({children}: {children?: ReactNode}): React.JSX.Element {
  return <kbd className={styles.kbd}>{children}</kbd>;
}
