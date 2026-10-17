// Whether a click on a zoomable element opens the lightbox.

import type {MouseEvent} from 'react';

/**
 * A click on the image or diagram (or Enter / Space on its zoom button)
 * opens the lightbox, except:
 * - a click on the toolbar, or on a link inside the element (a Mermaid
 *   diagram may have links), which does its own thing;
 * - a click from inside the lightbox (a React portal: its events bubble
 *   through the React tree, but it is not inside the element in the DOM);
 * - a pointer click while zoomed in place: it ends a drag (pan);
 * - the second click of a double click (the double click resets an in-place
 *   zoom, which may already be gone by the time that click arrives);
 * - a click that ends a text selection inside the element.
 */
export function shouldOpenOnClick(event: MouseEvent, element: HTMLElement | null, zoomedInPlace: boolean): boolean {
  const target = event.target;
  if (!element || !(target instanceof Element) || !element.contains(target)) {
    return false;
  }
  if (target.closest('[data-panzoom-ignore]')) {
    return false;
  }
  const link = target.closest('a');
  if (link && element.contains(link)) {
    return false;
  }
  const keyboard = event.detail === 0;
  if (keyboard) {
    return true;
  }
  if (event.detail > 1) {
    return false; // The second click of a double click (it resets an in-place zoom).
  }
  if (zoomedInPlace) {
    return false;
  }
  const selection = window.getSelection();
  if (selection && !selection.isCollapsed && selection.anchorNode && element.contains(selection.anchorNode)) {
    return false;
  }
  return true;
}
