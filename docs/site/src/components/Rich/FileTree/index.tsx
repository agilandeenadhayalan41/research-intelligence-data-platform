import React, {type ReactNode} from 'react';
import styles from './styles.module.css';

type FileTreeProps = {
  /**
   * A Markdown bulleted list, nested by indentation. An item with a nested
   * list is drawn as a folder, any other item as a file. Text after the name
   * (a dash and a comment) is shown as written.
   */
  children: ReactNode;
  /** Accessible name; defaults to "File tree". */
  label?: string;
};

/**
 * Renders a nested Markdown list as a file tree with folder and file icons
 * and guide lines. Pure CSS over the list the MDX already renders, so it
 * needs no browser APIs and stays a plain nested list on GitHub.
 */
export default function FileTree({children, label = 'File tree'}: FileTreeProps): React.JSX.Element {
  return (
    <div className={styles.tree} role="group" aria-label={label}>
      {children}
    </div>
  );
}
