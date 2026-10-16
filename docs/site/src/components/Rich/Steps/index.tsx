import React, {Children, isValidElement, type ReactNode} from 'react';
import clsx from 'clsx';
import styles from './styles.module.css';

type StepsProps = {
  /**
   * Either <Step> elements, or one Markdown ordered list (which stays a
   * plain numbered list on GitHub).
   */
  children: ReactNode;
  /**
   * false draws a dot instead of the number, for stages whose own names
   * carry numbers (for example "Step 09").
   */
  numbered?: boolean;
};

/** Vertical steps joined by a line: numbered circles, or dots. */
export function Steps({children, numbered = true}: StepsProps): React.JSX.Element {
  const hasSteps = Children.toArray(children).some(
    (child) => isValidElement(child) && child.type === Step,
  );
  return (
    <div className={clsx(styles.steps, !numbered && styles.dots)}>
      {hasSteps ? <ol className={styles.list}>{children}</ol> : children}
    </div>
  );
}

type StepProps = {
  /** Optional short title above the step's content. */
  title?: ReactNode;
  children?: ReactNode;
};

/** One step of <Steps>. Its content is Markdown: text, headings, code. */
export function Step({title, children}: StepProps): React.JSX.Element {
  return (
    <li className={styles.step}>
      {title != null && <p className={styles.title}>{title}</p>}
      {children}
    </li>
  );
}
