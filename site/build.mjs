import { createHash } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Marked } from 'marked';
import * as installer from './public/setup.mjs';

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

const marked = new Marked({ gfm: true });

function codeBlocks(heading) {
  return marked.lexer(section(heading)).filter((token) => token.type === 'code').map((token) => token.text);
}

const plain = (html) =>
  html.replace(/<[^>]+>/g, '').replace(/&(amp|lt|gt|quot);/g, (_, e) => ({ amp: '&', lt: '<', gt: '>', quot: '"' })[e]);
const withoutComments = (text) => text.split('\n').filter((line) => !line.trim().startsWith('#')).join('\n');

function checkInstall() {
  const readmeBlocks = { 'Getting started': codeBlocks('Getting started').map(withoutComments), Unraid: codeBlocks('Unraid') };
  for (const [method, heading] of [['compose', 'Getting started'], ['run', 'Getting started'], ['unraid', 'Unraid']]) {
    const view = installer.install('plex', method);
    for (const code of [view.code, view.start].filter(Boolean)) {
      if (!readmeBlocks[heading].includes(plain(code))) fail(`public/setup.mjs ${method} code differs from README.md "${heading}"`);
    }
  }
}

const tagline = readme.match(/<b>(.+?)<\/b>/)?.[1] ?? fail('README.md has no bold tagline');
const version = changelog.match(/^## \[(\d+\.\d+\.\d+)\]/m)?.[1] ?? fail('CHANGELOG.md has no released version');

checkInstall();
const settingsData = JSON.parse(readFileSync(join(here, 'settings.json'), 'utf8'));
const firstView = installer.install('plex', 'compose');
const firstVals = installer.defaults(settingsData);
const firstLines = installer.lines(settingsData, firstVals);

const card = readFileSync(join(root, 'docs', 'social-card.png'));
const socialCard = `${site}/img/social-card.png?v=${createHash('sha256').update(card).digest('hex').slice(0, 8)}`;

const description =
  'Automatic textless posters for Plex, Jellyfin and Emby: the title in one spot, quality badges and streaming marks. ' +
  'Free and self-hosted in Docker.';

const schema = {
  '@context': 'https://schema.org',
  '@type': 'SoftwareApplication',
  name: 'Posteryard',
  description,
  url: `${site}/`,
  image: socialCard,
  applicationCategory: 'MultimediaApplication',
  operatingSystem: 'Docker',
  softwareVersion: version,
  offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
  sameAs: [repo],
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
  social_card: socialCard,
  version,
  year: String(new Date().getFullYear()),
  schema: JSON.stringify(schema).replaceAll('<', '\\u003c'),
  install_server: installer.servers.plex.label,
  install_method: installer.methods.compose.label,
  install_fname: firstView.fname,
  install_code: firstView.code,
  install_start: firstView.start,
  install_fill: firstView.fill,
  install_next: firstView.next,
  s9_tabs: installer.tabs(settingsData, 0, firstVals),
  s9_rows: installer.rows(settingsData, 0, firstVals, 'plex'),
  s9_lines: firstLines.html,
  s9_foot: firstLines.foot,
  s9_reset_hidden: firstLines.changed ? '' : ' hidden',
  s9_copy_hidden: firstLines.count ? '' : ' hidden',
  settings_json: JSON.stringify(settingsData).replaceAll('<', '\\u003c'),
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
