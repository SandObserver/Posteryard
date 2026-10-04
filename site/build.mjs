import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Marked } from 'marked';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const dist = join(here, 'dist');
const site = 'https://posteryard.sandobserver.com';
const repo = 'https://github.com/SandObserver/Posteryard';

const readme = readFileSync(join(root, 'README.md'), 'utf8');
const changelog = readFileSync(join(root, 'CHANGELOG.md'), 'utf8');

function fail(message) {
  console.error(`site: ${message}`);
  process.exit(1);
}

function section(heading) {
  const match = readme.match(new RegExp(`^## ${heading}\\n([\\s\\S]*?)(?=^## |(?![\\s\\S]))`, 'm'));
  if (!match) fail(`README.md has no "## ${heading}" section`);
  return match[1].trim();
}

function escape(text) {
  return text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
}

const onPage = new Set(['getting-started', 'settings']);

function href(link) {
  if (/^[a-z]+:/i.test(link)) return link;
  if (link.startsWith('#')) return onPage.has(link.slice(1)) ? link : `${repo}${link}`;
  return `${repo}/blob/main/${link}`;
}

const codeLabels = { yaml: 'compose.yml', sh: 'Terminal', json: 'Response', text: 'Output' };

const marked = new Marked({
  gfm: true,
  renderer: {
    link({ href: target, tokens }) {
      const url = href(target);
      const text = this.parser.parseInline(tokens);
      const external = url.startsWith('http') ? ' target="_blank" rel="noopener noreferrer"' : '';
      return `<a href="${url}"${external}>${text}</a>`;
    },
    code({ text, lang }) {
      const label = codeLabels[lang] ?? lang ?? 'Code';
      return (
        `<div class="code"><div class="code-bar"><span>${escape(label)}</span>` +
        `<button type="button" class="copy" aria-label="Copy ${escape(label)}">Copy</button></div>` +
        `<pre><code>${escape(text).replace(/^(\s*)(#.*)$/gm, '$1<span class="cm">$2</span>')}</code></pre></div>`
      );
    },
    table({ header, rows }) {
      const names = header.map((cell) => cell.text.toLowerCase());
      const items = rows.map((cells) => {
        const [term, ...rest] = cells.map((cell) => this.parser.parseInline(cell.tokens));
        const details = rest
          .map((html, i) => {
            if (!html) return '';
            return names[i + 1] === 'default'
              ? `<span class="default">Default ${html}</span>`
              : `<p>${html}</p>`;
          })
          .join('');
        return `<div class="def"><dt>${term}</dt><dd>${details}</dd></div>`;
      });
      return `<dl class="defs">${items.join('')}</dl>`;
    },
  },
});

function steps(markdown) {
  const groups = [];
  let lead = [];
  for (const token of marked.lexer(markdown)) {
    if (token.type === 'heading' && token.depth === 3) groups.push({ title: token.text, tokens: [] });
    else if (groups.length) groups.at(-1).tokens.push(token);
    else lead.push(token);
  }
  if (!groups.length) fail('README.md Getting started has no ### step headings');
  lead.links = {};
  const items = groups.map((group) => {
    group.tokens.links = {};
    return `<li class="step"><h3>${marked.parseInline(group.title)}</h3>${marked.parser(group.tokens)}</li>`;
  });
  return { lead: marked.parser(lead), steps: `<ol class="steps">${items.join('')}</ol>` };
}

const tagline = readme.match(/<b>(.+?)<\/b>/)?.[1] ?? fail('README.md has no bold tagline');
const version = changelog.match(/^## \[(\d+\.\d+\.\d+)\]/m)?.[1] ?? fail('CHANGELOG.md has no released version');

const setup = steps(section('Getting started'));
const settings = section('Settings');
const settingsCount = settings.split('\n').filter((line) => line.startsWith('| `')).length;
if (!settingsCount) fail('README.md Settings section has no settings table');

const description =
  'Automatic textless posters for Plex and Jellyfin: the title in one spot, quality badges and streaming marks. ' +
  'Free and self-hosted in Docker.';

const schema = {
  '@context': 'https://schema.org',
  '@type': 'SoftwareApplication',
  name: 'Posteryard',
  description,
  url: `${site}/`,
  image: `${site}/img/social-card.png`,
  applicationCategory: 'MultimediaApplication',
  operatingSystem: 'Docker',
  softwareVersion: version,
  offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
  codeRepository: repo,
  author: { '@type': 'Person', name: 'SandObserver', url: 'https://github.com/SandObserver' },
};

const LIBRARY = 12;
const posters = JSON.parse(readFileSync(join(here, 'posters.json'), 'utf8'));
for (const poster of posters) {
  for (const size of ['', '-240']) {
    if (!existsSync(join(here, 'public', 'img', 'posters', `${poster.id}${size}.webp`))) fail(`posters.json lists ${poster.id}, which has no ${poster.id}${size}.webp`);
  }
}

const sortKey = (title) => title.toLowerCase().replace(/^the /, '');

function library() {
  const day = Number(new Date().toISOString().slice(0, 10).replaceAll('-', ''));
  const kind = day % 2 ? 'tv' : 'movie';
  const pool = posters.filter((poster) => poster.kind === kind);
  for (const k of ['movie', 'tv']) {
    if (posters.filter((poster) => poster.kind === k).length < LIBRARY) fail(`posters.json needs at least ${LIBRARY} ${k} posters`);
  }
  const picks = Array.from({ length: LIBRARY }, (_, i) => pool[(day * 7 + i) % pool.length]).sort((a, b) =>
    sortKey(a.title).localeCompare(sortKey(b.title)),
  );
  const tiles = picks.map((poster, i) => {
    const sub = kind === 'movie' ? String(poster.year) : `${poster.seasons} ${poster.seasons === 1 ? 'Season' : 'Seasons'}`;
    return (
      `<figure class="show${i === 0 ? ' focus' : ''}"><div class="art"><noscript>` +
      `<img src="/img/posters/${poster.id}.webp" srcset="/img/posters/${poster.id}-240.webp 240w, /img/posters/${poster.id}.webp 400w" sizes="(max-width: 600px) 30vw, 145px" alt="" width="400" height="600" /></noscript></div>` +
      `<figcaption>${escape(poster.title)}<span>${sub}</span></figcaption></figure>`
    );
  });
  return { grid: tiles.join('') };
}

const preview = library();

const values = {
  site,
  repo,
  tagline,
  description,
  version,
  schema: JSON.stringify(schema).replaceAll('<', '\\u003c'),
  getting_started_lead: setup.lead,
  getting_started: setup.steps,
  settings_count: String(settingsCount),
  settings: marked.parse(settings),
  tv_grid: preview.grid,
  poster_pool: JSON.stringify(posters).replaceAll('<', '\\u003c'),
};

function fill(file) {
  return readFileSync(join(here, file), 'utf8').replace(/\{\{(\w+)\}\}/g, (_, key) => {
    if (!(key in values)) fail(`${file} uses an unknown value {{${key}}}`);
    return values[key];
  });
}

rmSync(dist, { recursive: true, force: true });
mkdirSync(dist, { recursive: true });
cpSync(join(here, 'public'), dist, { recursive: true });
cpSync(join(root, 'docs', 'logo.svg'), join(dist, 'logo.svg'));
cpSync(join(root, 'docs', 'social-card.png'), join(dist, 'img', 'social-card.png'));
writeFileSync(join(dist, 'index.html'), fill('template.html'));
writeFileSync(join(dist, '404.html'), fill('404.html'));
writeFileSync(join(dist, 'robots.txt'), `User-agent: *\nAllow: /\n\nSitemap: ${site}/sitemap.xml\n`);
writeFileSync(
  join(dist, 'sitemap.xml'),
  `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    `  <url><loc>${site}/</loc></url>\n</urlset>\n`,
);
console.log(`site: built Posteryard ${version} into dist/`);
