// Components every doc and blog post can use without an import. They are
// registered in src/theme/MDXComponents.tsx; scripts/check-mdx.mjs lists the
// same names (GLOBAL_COMPONENTS). See the "Writing docs" section of the README.
export {default as Badge} from './Badge';
export {default as Callout, type CalloutType} from './Callout';
export {Card, Cards, type CardProps} from './Cards';
export {default as FileTree} from './FileTree';
export {default as Kbd} from './Kbd';
export {Step, Steps} from './Steps';
