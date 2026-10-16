// Types for the one react-dom API the site uses (createPortal, for the zoom
// lightbox). The site does not install @types/react-dom; delete this file if
// that package is added.

declare module 'react-dom' {
  export function createPortal(
    children: import('react').ReactNode,
    container: Element | DocumentFragment,
    key?: string | null,
  ): import('react').ReactPortal;
}
