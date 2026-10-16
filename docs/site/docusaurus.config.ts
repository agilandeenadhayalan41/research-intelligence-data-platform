import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

import repoLinks from './src/remark/repo-links.mjs';
import {DEFAULT_EXCLUDED_AUTHORS} from './src/lib/github.mjs';

// This file runs in Node.js at build time. Do not use browser APIs or JSX here.

const REPO_URL =
  'https://github.com/agilandeenadhayalan41/research-intelligence-data-platform';
const EDIT_URL_BASE = `${REPO_URL}/edit/main/docs/`;
const REPO = {
  owner: 'agilandeenadhayalan41',
  name: 'research-intelligence-data-platform',
  branch: 'main',
};

// Public URL of the deployed site. Vercel sets VERCEL_PROJECT_PRODUCTION_URL
// (a bare host name, no scheme) on every build; SITE_URL overrides it.
function resolveSiteUrl(): string {
  if (process.env.SITE_URL) {
    return process.env.SITE_URL;
  }
  if (process.env.VERCEL_PROJECT_PRODUCTION_URL) {
    return `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}`;
  }
  return 'http://localhost:3000';
}

const config: Config = {
  title: 'Research Intelligence Data Platform',
  tagline:
    'BigQuery-first research intelligence on public OpenAlex data: contracts, pipelines, evidence and roadmap.',
  favicon: 'img/logo.svg',

  url: resolveSiteUrl(),
  baseUrl: '/',

  // Strict link checking: a broken page link, anchor or Markdown link fails
  // the build instead of shipping a dead link.
  onBrokenLinks: 'throw',
  onBrokenAnchors: 'throw',
  onDuplicateRoutes: 'throw',

  // Docusaurus v4 behaviour, except "faster" (Rspack/SWC native binaries),
  // which this site does not install.
  future: {
    v4: {
      removeLegacyPostBuildHeadAttribute: true,
      useCssCascadeLayers: true,
      siteStorageNamespacing: true,
      fasterByDefault: false,
      mdx1CompatDisabledByDefault: true,
    },
  },

  markdown: {
    // .md files are parsed as CommonMark (not MDX), so existing docs that
    // contain "<" or "{" render as they do on GitHub. .mdx files stay MDX.
    format: 'detect',
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: 'throw',
      onBrokenMarkdownImages: 'throw',
    },
  },

  themes: ['@docusaurus/theme-mermaid'],

  // Build-time page metadata (reading time, git history) for every doc and
  // blog post, read by the page components through usePluginData.
  plugins: ['./src/plugins/page-meta/index.js'],

  // Read in the browser through useDocusaurusContext().siteConfig.customFields.
  customFields: {
    repo: REPO,
    // Where likes, comments and editor drafts are kept: 'browser' stores them
    // permanently in each visitor's own browser (IndexedDB). A remote adapter
    // (an API plus a database) can be added later behind the same interface.
    communityStore: 'browser',
    // Page history ("Updated ... by", contributors, commit counts) credits
    // people only. Commits whose GitHub login or git author name is in this
    // list are left out, like GitHub bot accounts, "[bot]" names and
    // @anthropic.com or "[bot]@users.noreply.github.com" author emails
    // (always left out). Case, a trailing "[bot]", spaces, hyphens,
    // underscores and dots are ignored. Remove the key for the default list.
    pageHistory: {
      excludeAuthors: [...DEFAULT_EXCLUDED_AUTHORS],
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          // Content is read in place from the repository's docs/ folder.
          path: '..',
          include: ['**/*.{md,mdx}'],
          exclude: [
            // This Docusaurus project (its README, node_modules, build output).
            'site/**',
            // Empty placeholder folders.
            '**/.gitkeep',
            // Docusaurus defaults.
            '**/_*.{js,jsx,ts,tsx,md,mdx}',
            '**/_*/**',
            '**/*.test.{js,jsx,ts,tsx}',
            '**/__tests__/**',
          ],
          routeBasePath: '/',
          sidebarPath: './sidebars.ts',
          // Function form: the string form would join the path relative to
          // this site folder and produce ".../docs/../architecture/x.md".
          // docPath is relative to the docs/ folder, e.g. architecture/x.md.
          editUrl: ({docPath}) => `${EDIT_URL_BASE}${docPath}`,
          // Must run before Docusaurus resolves Markdown links, so links that
          // leave docs/ become GitHub URLs instead of broken site links.
          beforeDefaultRemarkPlugins: [repoLinks],
        },
        // Blog posts live in docs/site/blog (outside the docs plugin's folder
        // set, which excludes site/**). Editable in the browser at /blog/editor.
        blog: {
          path: './blog',
          routeBasePath: 'blog',
          showReadingTime: true,
          blogSidebarTitle: 'Recent posts',
          blogSidebarCount: 'ALL',
          editUrl: `${REPO_URL}/edit/main/docs/site/`,
          onInlineAuthors: 'ignore',
          onUntruncatedBlogPosts: 'ignore',
        },
        // React and MDX pages in src/pages: the home page and the blog editor.
        pages: {
          path: './src/pages',
        },
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    colorMode: {
      respectPrefersColorScheme: true,
    },
    docs: {
      sidebar: {
        hideable: true,
      },
    },
    navbar: {
      title: 'Research Intelligence',
      logo: {
        alt: 'Research Intelligence Data Platform',
        src: 'img/logo.svg',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docs',
          position: 'left',
          label: 'Docs',
        },
        // Plain links, not `type: 'doc'`: a doc item counts as active on every
        // page of its sidebar, and all docs share one sidebar, so Docs,
        // Status and Glossary would all be highlighted together. The strict
        // link check still fails the build if either page goes away.
        {to: '/overview/status', label: 'Status', position: 'left'},
        {to: '/overview/glossary', label: 'Glossary', position: 'left'},
        // Last tab of the top bar.
        {to: '/blog', label: 'Blog', position: 'left'},
        {
          href: REPO_URL,
          label: 'GitHub',
          position: 'right',
        },
        // Optional GitHub sign-in: avatar and username once signed in.
        {type: 'custom-githubUser', position: 'right'},
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {label: 'Introduction', to: '/overview/introduction'},
            {label: 'How it works', to: '/overview/how-it-works'},
            {label: 'Status', to: '/overview/status'},
            {label: 'Roadmap', to: '/architecture/roadmap'},
            {label: 'Getting started', to: '/overview/getting-started'},
            {label: 'Glossary', to: '/overview/glossary'},
          ],
        },
        {
          title: 'Community',
          items: [
            {label: 'Blog', to: '/blog'},
            {label: 'Write a post', to: '/blog/editor'},
          ],
        },
        {
          title: 'Repository',
          items: [
            {label: 'GitHub', href: REPO_URL},
            {label: 'Issues', href: `${REPO_URL}/issues`},
          ],
        },
      ],
      copyright: `Research Intelligence Data Platform. Built with Docusaurus.`,
    },
    mermaid: {
      theme: {light: 'neutral', dark: 'dark'},
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'powershell', 'csv', 'diff', 'toml'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
