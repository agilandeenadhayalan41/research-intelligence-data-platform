// A short status note (e.g. "Use Save image as… to download") that clears
// itself after a few seconds.

import {useCallback, useEffect, useRef, useState} from 'react';

const NOTE_MS = 6000;

export function useNote(): [string, (message: string) => void] {
  const [note, setNote] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const show = useCallback((message: string) => {
    setNote(message);
    if (timer.current) {
      clearTimeout(timer.current);
    }
    timer.current = setTimeout(() => setNote(''), NOTE_MS);
  }, []);
  useEffect(
    () => () => {
      if (timer.current) {
        clearTimeout(timer.current);
      }
    },
    [],
  );
  return [note, show];
}
