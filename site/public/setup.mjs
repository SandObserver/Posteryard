const REPO = 'https://github.com/SandObserver/Posteryard';
const EXT = '<svg class="ext" aria-hidden="true"><use href="#i-ext"/></svg>';
const CHEV = '<svg aria-hidden="true"><use href="#i-chevrons"/></svg>';
const DATA = './data';

export const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const V = (t) => `<span class="v">${esc(t)}</span>`;
const link = (href, text) => `<a href="${href}" target="_blank" rel="noopener noreferrer">${text}${EXT}</a>`;

export const servers = {
  plex: { label: 'Plex', url: ['PLEX_URL', 'http://192.168.1.10:32400'], key: ['PLEX_TOKEN', 'your-plex-token'], keyHelp: 'Plex token', keyUrl: 'https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/', live: 'uploads the images and locks them, so a Plex refresh keeps them.' },
  jellyfin: { label: 'Jellyfin', url: ['JELLYFIN_URL', 'http://192.168.1.10:8096'], key: ['JELLYFIN_API_KEY', 'your-jellyfin-api-key'], keyHelp: 'Jellyfin API key', keyUrl: `${REPO}/blob/main/docs/jellyfin-and-emby.md#jellyfin`, live: 'uploads the images. Jellyfin keeps them unless a refresh uses Replace existing images.' },
  emby: { label: 'Emby', url: ['EMBY_URL', 'http://192.168.1.10:8096'], key: ['EMBY_API_KEY', 'your-emby-api-key'], keyHelp: 'Emby API key', keyUrl: `${REPO}/blob/main/docs/jellyfin-and-emby.md#emby`, live: 'uploads the images. Emby keeps them unless a refresh uses Replace existing images.' },
};
export const methods = { compose: { label: 'Docker Compose' }, run: { label: 'docker run' }, unraid: { label: 'Unraid' } };
export const pickers = { server: { name: 'Media server', options: servers }, method: { name: 'Install method', options: methods } };

function compose(x) {
  return `services:
  posteryard:
    image: ghcr.io/sandobserver/posteryard:latest
    container_name: posteryard
    restart: unless-stopped
    init: true
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    environment:
      - TMDB_API_KEY=${V('your-tmdb-api-key')}
      - ${x.url[0]}=${V(x.url[1])}
      - ${x.key[0]}=${V(x.key[1])}
      - WEBHOOK_SECRET=${V('any-long-random-text')}
      - TZ=${V('America/New_York')}
      - DRY_RUN=true
    volumes:
      - ${V(DATA)}:/data
    ports:
      - "8000:8000"`;
}
function run(x) {
  return `mkdir -p ${V(DATA)} &amp;&amp; sudo chown 1000:1000 ${V(DATA)}
docker run -d --name posteryard --restart unless-stopped \\
  --init --read-only --tmpfs /tmp --cap-drop ALL \\
  --security-opt no-new-privileges:true \\
  -e TMDB_API_KEY=${V('your-tmdb-api-key')} \\
  -e ${x.url[0]}=${V(x.url[1])} \\
  -e ${x.key[0]}=${V(x.key[1])} \\
  -e WEBHOOK_SECRET=${V('any-long-random-text')} \\
  -e TZ=${V('America/New_York')} -e DRY_RUN=true \\
  -v ${V(DATA)}:/data -p 8000:8000 \\
  ghcr.io/sandobserver/posteryard:latest`;
}
const unraid = () => `curl -fsSL -o /boot/config/plugins/dockerMan/templates-user/my-Posteryard.xml \\
  https://raw.githubusercontent.com/SandObserver/Posteryard/main/templates/posteryard.xml`;

export function install(server, method) {
  const x = servers[server], m = method;
  const keys = `${link('https://www.themoviedb.org/settings/api', 'TMDB API key')} · ${link(x.keyUrl, x.keyHelp)}`;
  const where = m === 'unraid' ? '<code>/mnt/user/appdata/posteryard/previews</code>' : m === 'compose' ? '<code>data/previews</code>, next to <code>compose.yml</code>' : '<code>data/previews</code>, in the folder you ran it from';
  const goLive = m === 'compose' ? 'change <code>DRY_RUN=true</code> to <code>false</code> in <code>compose.yml</code> and run <code>docker compose up -d</code> again'
    : m === 'run' ? 'remove the container with <code>docker rm -f posteryard</code> and run the same command with <code>DRY_RUN=false</code>'
    : 'open the container\'s <b>Edit</b> screen, set <b>Dry Run</b> to <code>false</code> and choose <b>Apply</b>';
  const logs = m === 'unraid' ? 'The container\'s <b>Logs</b> show progress.' : '<code>docker logs -f posteryard</code> shows progress.';
  return {
    fname: { compose: 'compose.yml', run: 'Terminal', unraid: 'Unraid terminal' }[m],
    code: m === 'compose' ? compose(x) : m === 'run' ? run(x) : unraid(),
    start: m === 'compose' ? `mkdir -p ${V(DATA)} &amp;&amp; sudo chown 1000:1000 ${V(DATA)}\ndocker compose up -d` : '',
    fill: m === 'unraid'
      ? `Then <b>Docker › Add Container › Template › Posteryard</b> and fill in the TMDB API key, the ${x.label} address and key, and a webhook secret. <b>Data</b> is the share where Posteryard keeps its files. Need the keys? ${keys}`
      : `Replace the yellow values. ${V(DATA)} is the folder on your server where Posteryard keeps its files; any path works. Need the keys? ${keys}`,
    next: `<li><div class="irow top"><svg class="si" aria-hidden="true"><use href="#sy-solid-p1-pre"/></svg><span class="imain"><span class="iname">First, a preview. ${x.label} is not changed.</span><span class="itext">Posteryard saves each poster to ${where}. Open a few to check them. ${logs}</span></span></div></li>`
      + `<li><div class="irow top"><svg class="si" aria-hidden="true"><use href="#sy-solid-p1-live"/></svg><span class="imain"><span class="iname">Then go live.</span><span class="itext">When you like them, ${goLive}. Posteryard ${x.live}</span></span></div></li>`,
    summary: `${x.label} with ${methods[m].label}`,
  };
}

export const inline = (text) => esc(text)
  .replace(/`([^`]+)`/g, '<code>$1</code>')
  .replace(/\[([^\]]+)\]\(((?:docs\/[\w-]+\.md)?#[\w-]+)\)/g, (_, label, target) =>
    link(target.startsWith('#') ? `${REPO}${target}` : `${REPO}/blob/main/${target}`, label));

export const flatten = (data) => data.groups.flatMap((g) => g.settings);
export const defaults = (data) => Object.fromEntries(flatten(data).map((s) => [s.key, s.default]));
export const isChanged = (s, vals) => (vals[s.key].trim() || s.default) !== s.default;

export function shown(s, vals) {
  const v = vals[s.key];
  if (s.type === 'multi') {
    const on = v.split(',').filter(Boolean);
    return on.length ? on.map((x) => s.options.find((o) => o[0] === x)[1]).join(', ') : 'None';
  }
  return s.options?.find((o) => o[0] === v)?.[1] ?? v;
}

export function tabs(data, active, vals) {
  return data.groups.map(({ name, settings }, i) => {
    const changed = settings.some((s) => isChanged(s, vals));
    return `<button role="tab" type="button" id="s9-tab-${i}" aria-controls="s9-panel" aria-selected="${i === active}" tabindex="${i === active ? 0 : -1}" data-i="${i}">${name}${changed ? '<i class="dot" aria-hidden="true"></i><span class="sr-only">, changed</span>' : ''}</button>`;
  }).join('');
}

function help(s, server) {
  if (!s.help) return '';
  if (typeof s.help === 'string') return inline(s.help);
  return inline(server === 'plex' ? s.help.plex : s.help.other).replace('{server}', servers[server].label);
}

export function rows(data, active, vals, server) {
  return data.groups[active].settings.map((s) => {
    const v = vals[s.key], changed = isChanged(s, vals);
    const id = `set-${s.key.toLowerCase()}`;
    let ctl;
    if (s.type === 'switch') ctl = `<label class="tog"><input type="checkbox" role="switch" data-k="${s.key}" ${v === 'true' ? 'checked' : ''} aria-labelledby="${id}"><span class="tr"></span></label>`;
    else if (s.type === 'time') ctl = `<button class="time-btn" type="button" aria-haspopup="dialog" aria-expanded="false" data-k="${s.key}" aria-label="${esc(s.label)} ${v}">${v}</button>`;
    else if (s.type === 'text') ctl = `<input class="field" type="text" spellcheck="false" autocomplete="off" data-k="${s.key}" value="${esc(v === s.default ? '' : v)}" placeholder="${esc(s.placeholder)}" aria-labelledby="${id}"${s.help ? ` aria-describedby="${id}-help"` : ''}>`;
    else ctl = `<button class="val-btn" type="button" aria-haspopup="listbox" aria-expanded="false" data-k="${s.key}" aria-label="${esc(s.label)}: ${esc(shown(s, vals))}"><span>${esc(shown(s, vals))}</span>${CHEV}</button>`;
    const note = help(s, server);
    return `<li><div class="irow${s.type === 'text' ? ' wide' : ''}${changed ? ' changed' : ''}" data-row="${s.key}"><span class="imain"><span class="iname" id="${id}">${esc(s.label)}</span><span class="ikey">${s.key}${changed ? '<span class="sr-only">, changed</span>' : ''}</span>${note ? `<span class="ihelp" id="${id}-help">${note}</span>` : ''}</span>${ctl}</div></li>`;
  }).join('');
}

export function lines(data, vals) {
  const all = flatten(data);
  const changed = all.filter((s) => isChanged(s, vals));
  const out = changed.map((s) => `- ${s.key}=${esc(vals[s.key].trim())}`);
  const events = all.find((s) => s.key === 'NOTIFY_EVENTS');
  const placeholder = isChanged(events, vals) && vals.NOTIFY_EVENTS !== '' && !vals.NOTIFY_URLS.trim();
  if (placeholder) out.push(`- NOTIFY_URLS=${V('your-alert-address')}`);
  return {
    html: out.length ? out.join('\n') : '<span class="c"># Everything is on its default.\n# Change a setting and its line appears here.</span>',
    count: out.length,
    changed: changed.length,
    foot: placeholder ? 'Replace the yellow value, then run <code>docker compose up -d</code>.'
      : out.length ? 'Then run <code>docker compose up -d</code>.'
      : `Server, keys and <code>DRY_RUN</code> are in the install block above. ${link(`${REPO}#settings`, 'Every setting, explained')}.`,
  };
}
