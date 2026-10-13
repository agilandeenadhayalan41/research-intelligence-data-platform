import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

import repoLinks from './src/remark/repo-links.mjs';

// This file runs in Node.js at build time. Do not use browser APIs or JSX here.

const REPO_URL =
  'https://github.com/agilandeenadhayalan41/research-intelligence-data-platform';
const EDIT_URL_BASE = `${REPO_URL}/edit/main/docs/`;

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
        blog: false,
        pages: false,
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
        {
          type: 'doc',
          docId: 'overview/status',
          position: 'left',
          label: 'Status',
        },
        {
          type: 'doc',
          docId: 'overview/glossary',
          position: 'left',
          label: 'Glossary',
        },
        {
          href: REPO_URL,
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {label: 'Introduction', to: '/'},
            {label: 'How it works', to: '/overview/how-it-works'},
            {label: 'Status', to: '/overview/status'},
            {label: 'Roadmap', to: '/architecture/roadmap'},
            {label: 'Getting started', to: '/overview/getting-started'},
            {label: 'Glossary', to: '/overview/glossary'},
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
