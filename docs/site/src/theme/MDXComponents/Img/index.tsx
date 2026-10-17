// Wraps the theme's Markdown/MDX image (used for ![alt](src) in docs, blog
// posts and pages, all parsed as MDX) with the image zoom: a lightbox and
// in-page zoom and pan (src/components/Zoom). The theme's MDXComponents map
// (`img`) points here, and src/theme/MDXComponents.tsx keeps that entry when
// it adds the site's rich components.
//
// An image whose title or class is "no-zoom" is rendered as before, without
// the zoom (the "no-zoom" title is not shown as a tooltip).

import React from 'react';
import Img from '@theme-original/MDXComponents/Img';
import type ImgType from '@theme/MDXComponents/Img';
import type {WrapperProps} from '@docusaurus/types';
import {isZoomOptOut} from '@site/src/lib/panzoom.mjs';
import ZoomableImage from '@site/src/components/Zoom/ZoomableImage';

type Props = WrapperProps<typeof ImgType>;

export default function ImgWrapper(props: Props): React.JSX.Element {
  if (isZoomOptOut({title: props.title, className: props.className})) {
    const {title, ...rest} = props;
    const keepTitle = typeof title === 'string' && title.trim().toLowerCase() !== 'no-zoom';
    return <Img {...rest} title={keepTitle ? title : undefined} />;
  }
  return (
    <ZoomableImage src={typeof props.src === 'string' ? props.src : undefined} alt={props.alt}>
      <Img {...props} />
    </ZoomableImage>
  );
}
