// Client access to the build-time data of the page-meta plugin
// (src/plugins/page-meta/index.js), published with setGlobalData and read
// with usePluginData('page-meta'). Keys are repository paths, the same as
// repoPathForDoc(metadata.source) from src/lib/github.mjs.

import {usePluginData} from '@docusaurus/useGlobalData';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';

export type BuildAuthor = {
  /** Git author name. */
  name: string;
  commits: number;
  lastDate: string | null;
  lastSha: string | null;
};

export type PageMetaEntry = {
  readingMinutes: number | null;
  words: number | null;
  lastAuthor: string | null;
  /** ISO 8601. */
  lastDate: string | null;
  lastSha?: string | null;
  /** Number of commits that changed the file; null when git had no answer. */
  commits: number | null;
  /** Most commits first; null when git had no answer. */
  authors: BuildAuthor[] | null;
};

export type BlogSource = {
  /** e.g. 'docs/site/blog/2026-10-16-welcome-to-the-blog.md' */
  repoPath: string;
  /** Path inside docs/site/blog, e.g. '2026-10-16-welcome-to-the-blog.md' */
  file: string;
  title: string;
  format: 'md' | 'mdx';
  /** Raw source, front matter included. */
  content: string;
};

export type PageMetaGlobalData = {
  version: number;
  pages: Record<string, PageMetaEntry>;
  blogSources: BlogSource[];
};

export type RepoConfig = {owner: string; name: string; branch: string};

/** The whole build-time data set, or null when the plugin is not loaded. */
export function usePageMetaData(): PageMetaGlobalData | null {
  const data = usePluginData('page-meta') as PageMetaGlobalData | undefined;
  return data && typeof data === 'object' && data.pages ? data : null;
}

/** Build-time data of one file, by repository path. */
export function usePageMeta(repoPath: string | null | undefined): PageMetaEntry | null {
  const data = usePageMetaData();
  if (!data || !repoPath) {
    return null;
  }
  return Object.prototype.hasOwnProperty.call(data.pages, repoPath) ? data.pages[repoPath] : null;
}

/** Raw sources of the blog posts (for the blog editor), newest file first. */
export function useBlogSources(): BlogSource[] {
  return usePageMetaData()?.blogSources ?? [];
}

/** customFields.repo from docusaurus.config.ts, or null when it is missing. */
export function useRepoConfig(): RepoConfig | null {
  const {siteConfig} = useDocusaurusContext();
  const repo = siteConfig.customFields?.repo as Partial<RepoConfig> | undefined;
  if (!repo || typeof repo.owner !== 'string' || typeof repo.name !== 'string') {
    return null;
  }
  return {
    owner: repo.owner,
    name: repo.name,
    branch: typeof repo.branch === 'string' && repo.branch ? repo.branch : 'main',
  };
}
