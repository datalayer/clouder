/** @type {import('@docusaurus/types').DocusaurusConfig} */
module.exports = {
  title: '☁️ Clouder',
  tagline: 'Create, manage and share Kubernetes clusters',
  url: 'https://clouder.sh',
  baseUrl: '/',
  clientModules: [
    require.resolve('./src/gtag-shim.js'),
  ],
  onBrokenLinks: 'throw',
  onBrokenMarkdownLinks: 'warn',
  favicon: 'img/favicon.ico',
  organizationName: 'datalayer', // Usually your GitHub org/user name.
  projectName: 'clouder', // Usually your repo name.
  markdown: {
    mermaid: true,
  },
  plugins: [
    '@docusaurus/theme-live-codeblock',
    'docusaurus-lunr-search',
  ],
  themes: [
    '@docusaurus/theme-mermaid',
  ],
  themeConfig: {
    colorMode: {
      defaultMode: 'light',
      disableSwitch: true,
    },
    navbar: {
      title: 'Clouder',
      logo: {
        alt: 'Datalayer Logo',
        src: 'img/datalayer/logo.svg',
      },
      items: [
        {
          type: 'doc',
          docId: 'architecture/index',
          position: 'left',
          label: 'Architecture',
        },
        {
          type: 'doc',
          docId: 'cli/index',
          position: 'left',
          label: 'CLI',
        },
        {
          type: 'doc',
          docId: 'cluster/index',
          position: 'left',
          label: 'Cluster',
        },
        {
          type: 'doc',
          docId: 'deployments/index',
          position: 'left',
          label: 'Deployments',
        },
        {
          type: 'doc',
          docId: 'services/index',
          position: 'left',
          label: 'Services',
        },
        {
          type: 'doc',
          docId: 'integrations/index',
          position: 'left',
          label: 'Integrations',
        },
        {
          type: 'doc',
          docId: 'management/index',
          position: 'left',
          label: 'Management',
        },
        {
          type: 'doc',
          docId: 'operations/index',
          position: 'left',
          label: 'Operations',
        },
        {
          type: 'doc',
          docId: 'benchmarks/index',
          position: 'left',
          label: 'Benchmarks',
        },
        {
          type: 'doc',
          docId: 'support/index',
          position: 'left',
          label: 'Support',
        },
        {
          type: 'doc',
          docId: 'glossary/index',
          position: 'left',
          label: 'Glossary',
        },
        {
          href: 'https://discord.gg/YQFwvmSSuR',
          position: 'right',
          className: 'header-discord-link',
          'aria-label': 'Discord',
        },
        {
          href: 'https://github.com/datalayer',
          position: 'right',
          className: 'header-github-link',
          'aria-label': 'GitHub',
        },
        {
          href: 'https://bsky.app/profile/datalayer.ai',
          position: 'right',
          className: 'header-bluesky-link',
          'aria-label': 'Bluesky',
        },
        {
          href: 'https://x.com/DatalayerIO',
          position: 'right',
          className: 'header-x-link',
          'aria-label': 'X',
        },
        {
          href: 'https://www.linkedin.com/company/datalayer',
          position: 'right',
          className: 'header-linkedin-link',
          'aria-label': 'LinkedIn',
        },
        {
          href: 'https://tiktok.com/@datalayerio',
          position: 'right',
          className: 'header-tiktok-link',
          'aria-label': 'TikTok',
        },
        {
          href: 'https://www.youtube.com/@datalayer',
          position: 'right',
          className: 'header-youtube-link',
          'aria-label': 'YouTube',
        },
        {
          href: 'https://datalayer.ai',
          position: 'right',
          className: 'header-datalayer-io-link',
          'aria-label': 'Datalayer',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {
              label: 'Clouder',
              to: '/',
            },
          ],
        },
        {
          title: 'Community',
          items: [
            {
              label: 'Bluesky',
              href: 'https://bsky.app/profile/datalayer.ai',
            },
            {
              label: 'GitHub',
              href: 'https://github.com/datalayer',
            },
            {
              label: 'LinkedIn',
              href: 'https://www.linkedin.com/company/datalayer',
            },
            {
              label: 'TikTok',
              href: 'https://tiktok.com/@datalayerio',
            },
            {
              label: 'YouTube',
              href: 'https://www.youtube.com/@datalayer',
            },
          ],
        },
        {
          title: 'More',
          items: [
            {
              label: 'Datalayer',
              href: 'https://datalayer.io',
            },
            {
              label: 'Datalayer Docs',
              href: 'https://docs.datalayer.ai',
            },
            {
              label: 'Datalayer Tech',
              href: 'https://datalayer.tech',
            },
            {
              label: 'Datalayer Guide',
              href: 'https://datalayer.guide',
            },
            {
              label: 'Datalayer Blog',
              href: 'https://datalayer.blog',
            },
            {
              label: 'Clouder',
              href: 'https://clouder.sh',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Datalayer, Inc.`,
    },
  },
  presets: [
    [
      '@docusaurus/preset-classic',
      {
        docs: {
          routeBasePath: '/',
          docItemComponent: '@theme/CustomDocItem',  
          sidebarPath: require.resolve('./sidebars.js'),
//          editUrl: 'https://github.com/datalayer/clouder/edit/main/',
        },
        theme: {
          customCss: require.resolve('./src/css/custom.css'),
        },
        gtag: {
          trackingID: 'G-ZQGMFNPPHT', 
          anonymizeIP: false,
        },
      },
    ],
  ],
};
