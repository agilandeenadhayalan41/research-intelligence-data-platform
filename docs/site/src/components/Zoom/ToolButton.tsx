// An icon button of the zoom toolbars. Disabled buttons use aria-disabled
// (not `disabled`), so a button that becomes disabled while focused (zoom in
// at the largest scale) keeps the focus.

import React from 'react';
import clsx from 'clsx';

import ZoomIcon, {type ZoomIconName} from './icons';
import styles from './styles.module.css';

export type ToolButtonProps = {
  icon: ZoomIconName;
  /** The accessible name and tooltip. */
  label: string;
  /** Visible text next to the icon (the label stays the accessible name). */
  text?: string;
  onClick: () => void;
  disabled?: boolean;
  className?: string;
  ref?: React.Ref<HTMLButtonElement>;
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'onClick' | 'disabled' | 'className'>;

export default function ToolButton({
  icon,
  label,
  text,
  onClick,
  disabled = false,
  className,
  ...rest
}: ToolButtonProps): React.JSX.Element {
  return (
    <button
      type="button"
      className={clsx(styles.toolButton, className)}
      aria-label={label}
      title={label}
      aria-disabled={disabled || undefined}
      onClick={() => {
        if (!disabled) {
          onClick();
        }
      }}
      {...rest}>
      <ZoomIcon name={icon} />
      {text ? <span aria-hidden="true">{text}</span> : null}
    </button>
  );
}
