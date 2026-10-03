import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Marked } from 'marked';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const dist = join(here, 'dist');
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

const onPage = new Set(['getting-started', 'settings']);

function href(link) {
  if (/^[a-z]+:/i.test(link)) return link;
  if (link.startsWith('#')) return onPage.has(link.slice(1)) ? link : `${repo}${link}`;
  return `${repo}/blob/main/${link}`;
}

const marked = new Marked({
  gfm: true,
  renderer: {
    link({ href: target, tokens }) {
      const url = href(target);
      const text = this.parser.parseInline(tokens);
      const external = url.startsWith('http') ? ' target="_blank" rel="noopener noreferrer"' : '';
      return `<a href="${url}"${external}>${text}</a>`;
    },
  },
});

function render(markdown) {
  return marked
    .parse(markdown)
    .replaceAll('<table>', '<div class="table-scroll"><table>')
    .replaceAll('</table>', '</table></div>');
}

const tagline = readme.match(/<b>(.+?)<\/b>/)?.[1] ?? fail('README.md has no bold tagline');
const version = changelog.match(/^## \[(\d+\.\d+\.\d+)\]/m)?.[1] ?? fail('CHANGELOG.md has no released version');

const [lead, ...setup] = section('Getting started').split('\n\n');
const settings = section('Settings');
const settingsCount = settings.split('\n').filter((line) => line.startsWith('| `')).length;
if (!settingsCount) fail('README.md Settings section has no settings table');

const values = {
  tagline,
  getting_started_lead: marked.parseInline(lead),
  getting_started: render(setup.join('\n\n')),
  settings_count: String(settingsCount),
  settings: render(settings),
  version,
  repo,
};

let html = readFileSync(join(here, 'template.html'), 'utf8');
html = html.replace(/\{\{(\w+)\}\}/g, (_, key) => {
  if (!(key in values)) fail(`template.html uses an unknown value {{${key}}}`);
  return values[key];
});

rmSync(dist, { recursive: true, force: true });
mkdirSync(dist, { recursive: true });
cpSync(join(here, 'public'), dist, { recursive: true });
cpSync(join(root, 'docs', 'logo.svg'), join(dist, 'logo.svg'));
cpSync(join(root, 'docs', 'social-card.png'), join(dist, 'img', 'social-card.png'));
writeFileSync(join(dist, 'index.html'), html);
console.log(`site: built Posteryard ${version} into dist/`);
