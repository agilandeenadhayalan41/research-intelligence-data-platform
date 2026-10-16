// Wraps the theme's Mermaid diagram with the diagram zoom: a lightbox,
// in-page zoom and pan, and SVG and PNG downloads (src/components/Zoom).

import React from 'react';
import Mermaid from '@theme-original/Mermaid';
import type MermaidType from '@theme/Mermaid';
import type {WrapperProps} from '@docusaurus/types';
import ZoomableDiagram from '@site/src/components/Zoom/ZoomableDiagram';

type Props = WrapperProps<typeof MermaidType>;

export default function MermaidWrapper(props: Props): React.JSX.Element {
  return (
    <ZoomableDiagram>
      <Mermaid {...props} />
    </ZoomableDiagram>
  );
}
