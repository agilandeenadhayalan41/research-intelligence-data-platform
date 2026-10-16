import React, {useState} from 'react';
import clsx from 'clsx';

export type AvatarSize = 'xs' | 'sm' | 'md' | 'lg' | 'xl';

const PIXELS: Record<AvatarSize, number> = {xs: 20, sm: 24, md: 32, lg: 40, xl: 48};

type Props = {
  /** https URL of the picture; initials are shown without one (or when it fails to load). */
  src?: string | null;
  /** Name or login, for the initials. */
  name: string;
  size?: AvatarSize;
  /** Empty (the default) when a visible name is next to the avatar. */
  alt?: string;
  className?: string;
};

/** Up to two initials from a name or login. */
export function initials(name: string): string {
  const words = name.trim().split(/[\s._-]+/u).filter(Boolean);
  const letters =
    words.length >= 2
      ? [Array.from(words[0])[0], Array.from(words[1])[0]]
      : Array.from(words[0] ?? '?').slice(0, 2);
  return letters.join('') || '?';
}

/** GitHub avatars accept a size parameter; ask for twice the shown size. */
function sizedSource(src: string, pixels: number): string | null {
  try {
    const url = new URL(src);
    if (url.protocol !== 'https:') {
      return null;
    }
    if (url.hostname === 'avatars.githubusercontent.com') {
      url.searchParams.set('s', String(pixels * 2));
    }
    return url.toString();
  } catch {
    return null;
  }
}

/** A round GitHub avatar (the rid-avatar classes in src/css/custom.css). */
export default function Avatar({src, name, size = 'md', alt = '', className}: Props): React.JSX.Element {
  const [failedSource, setFailedSource] = useState<string | null>(null);
  const pixels = PIXELS[size];
  const source = src ? sizedSource(src, pixels) : null;
  const classes = clsx('rid-avatar', `rid-avatar--${size}`, className);

  if (source && failedSource !== source) {
    return (
      <img
        className={classes}
        src={source}
        alt={alt}
        width={pixels}
        height={pixels}
        loading="lazy"
        decoding="async"
        referrerPolicy="no-referrer"
        onError={() => setFailedSource(source)}
      />
    );
  }
  return alt ? (
    <span className={classes} role="img" aria-label={alt}>
      {initials(name)}
    </span>
  ) : (
    <span className={classes} aria-hidden="true">
      {initials(name)}
    </span>
  );
}
