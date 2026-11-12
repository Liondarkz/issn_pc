// SPDX-License-Identifier: GPL-2.0-or-later
// The new launcher's page (issn 11-05). Everything it shows and changes goes through the Python
// side (launcher/issn_webui.py, window.pywebview.api); opened in a plain browser it uses the
// sample data at the end of this file, for working on the look.
'use strict';

const ICONS = {
  home: 'M7 4.5v15l13-7.5z',
  picture: 'M3 5h18v12H3zM8 21h8M12 17v4',
  controls: 'M6 9h4M8 7v4M15 10h.01M18 8h.01M7 15c-2.5 0-4-1.5-4-4l1-3c.8-2 2.5-3 4.5-3h7c2 0 3.7 1 4.5 3l1 3c0 2.5-1.5 4-4 4l-2-2H9z',
  game: 'M3 7h6l2 2h10v10H3zM3 7v12',
  trophies: 'M8 4h8v5a4 4 0 0 1-8 0zM8 6H5v1a3 3 0 0 0 3 3M16 6h3v1a3 3 0 0 1-3 3M12 13v4M8 20h8',
  news: 'M4 5h13v14H6a2 2 0 0 1-2-2zM17 9h3v8a2 2 0 0 1-2 2M8 9h5M8 13h5',
  customize: 'M14 4l6 6-8 8H6v-6zM4 20l2-2',
  help: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.7M12 17h.01',
  graphics: 'M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4',
  files: 'M10 3h4v3a2 2 0 1 0 4 0V3h3v7h-3a2 2 0 1 0 0 4h3v7h-7v-3a2 2 0 1 0-4 0v3H3v-7h3a2 2 0 1 0 0-4H3V3z',
  pc: 'M7 7h10v10H7zM9 3v4M15 3v4M9 17v4M15 17v4M3 9h4M3 15h4M17 9h4M17 15h4',
  advanced: 'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z',
};

let api = null;
let state = null;
let page = 'home';
let T = {};
const $ = (id) => document.getElementById(id);
const t = (text) => (T[text] !== undefined ? T[text] : text);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) if (child !== null && child !== undefined) node.append(child);
  return node;
}

function icon(name, size = 18) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', size); svg.setAttribute('height', size); svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor'); svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round'); svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', ICONS[name] || ICONS.help);
  svg.append(path);
  return svg;
}

function toast(text) {
  if (!text) return;
  const box = $('toast');
  box.textContent = text;
  box.classList.add('on');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => box.classList.remove('on'), 4200);
}

// ---- the side bar -----------------------------------------------------------------------------
function renderNav() {
  for (const [holder, expert] of [[$('nav'), false], [$('nav-expert'), true]]) {
    holder.replaceChildren(...state.pages.filter((p) => !!p.expert === expert).map((p) =>
      el('button', { type: 'button', class: 'nav-row' + (p.id === page ? ' on' : ''), 'data-page': p.id, onclick: () => show(p.id) },
        el('span', { class: 'mark' }), icon(p.id), el('span', { text: p.title }))));
    if (expert) holder.style.display = state.expert ? '' : 'none';
  }
  $('expert-switch').setAttribute('aria-pressed', state.expert ? 'true' : 'false');
}

async function show(id) {
  const info = state.pages.find((p) => p.id === id);
  if (info && info.expert && !state.expert) await setExpert(true);
  page = id;
  if (id !== 'home') await buildPage(id);  // always fresh: other pages and downloads change their data
  document.querySelectorAll('.page').forEach((p) => p.classList.toggle('on', p.id === 'page-' + id));
  renderNav();
}

// The answer of an action: a message, a question, a folder to pick, a page to open or rebuild.
async function handle(result) {
  if (!result) return;
  if (result.toast) toast(result.toast);
  if (result.message) toast(result.message);
  if (result.alert) await ask(result.alert, false);
  if (result.confirm) {
    const c = result.confirm;
    if (await ask(c.text, true)) await handle(await api.action(c.action, c.args || []));
    else if (c.no) await handle(await api.action(c.no.action, c.no.args || []));
  }
  if (result.pick) {
    const chosen = await api.choose_folder('', '');
    if (chosen) await handle(await api.action(result.pick.action, [...(result.pick.args || []), chosen]));
  }
  if (result.page && result.page !== page) await show(result.page);
  else if (result.reload && page !== 'home') await buildPage(page);
  if (result.reload || result.page) await refresh(false);
  if (result.play) await pressPlay();
}

// A question (yes / no) or a message (OK) over the page.
function ask(text, question) {
  return new Promise((resolve) => {
    const close = (answer) => { box.remove(); resolve(answer); };
    const box = el('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true' },
      el('div', { class: 'modal-box' }, el('p', { text }),
        el('div', { class: 'modal-buttons' },
          question ? el('button', { type: 'button', class: 'btn', text: t('No'), onclick: () => close(false) }) : null,
          el('button', { type: 'button', class: 'btn primary', text: question ? t('Yes') : t('OK'), onclick: () => close(true) }))));
    document.body.append(box);
    box.querySelector('.btn.primary').focus();
  });
}

async function runButton(b, arg) {
  if (b.copy !== null && b.copy !== undefined) {
    try { await navigator.clipboard.writeText(b.copy); toast(t('Copied ✓')); } catch (e) { toast(b.copy); }
    return;
  }
  if (b.url) { await api.action('open_url', [b.url]); return; }
  if (!b.action) return;
  if (b.confirm && !(await ask(b.confirm, true))) return;
  const args = [...(b.args || [])];
  if (arg !== undefined) args.push(arg);
  await handle(await api.action(b.action, args));
}

function buttonRow(list, arg) {
  return el('div', { class: 'button-row' }, ...list.filter(Boolean).map((b) =>
    el('button', { type: 'button', class: 'btn' + (b.primary ? ' primary' : ''), text: b.label, onclick: () => runButton(b, arg ? arg() : undefined) })));
}

async function setExpert(on) {
  state.expert = on;
  await api.set_value('expert_view', 'app', on);
  if (!on && state.pages.find((p) => p.id === page && p.expert)) await show('home');
  renderNav();
}

// ---- the Play page ----------------------------------------------------------------------------
function renderHome() {
  const home = state.home;
  $('hero-art').style.backgroundImage = home.art ? `url("${home.art}")` : '';
  $('hero-chip').textContent = home.chip || '';
  if (home.title) $('hero-title').textContent = home.title;
  const pick = $('hero-game');
  pick.replaceChildren(...(home.games || []).map((g) => el('option', { value: g.id, text: g.label })));
  pick.value = home.game || 'second_son';
  pick.hidden = !(home.games || []).length;  // First Light only where it was already chosen
  pick.disabled = !!state.running;
  pick.onchange = async () => handle(await api.action('choose_game', [pick.value]));
  $('hero-sub').textContent = home.sub || '';
  $('tagline').textContent = t('THEME') + ' ' + (state.themes.find((th) => th.id === state.theme) || {}).name?.toUpperCase();
  $('checks').replaceChildren(...home.checks.map((c) =>
    el('button', { type: 'button', class: 'check', onclick: () => c.page && show(c.page) },
      el('span', { class: 'dot ' + c.state, text: c.state === 'ok' ? '✓' : c.state === 'bad' ? '✕' : '!' }),
      el('span', { class: 'text' }, el('b', { text: c.name }), el('span', { text: ' · ' + c.text })),
      c.page ? el('span', { class: 'go', 'aria-hidden': 'true', text: '›' }) : null)));
  $('goals').replaceChildren(...home.goals.map((g) =>
    el('button', { type: 'button', class: 'goal' + (g.id === home.goal ? ' on' : ''), onclick: () => applyGoal(g.id) },
      g.label, el('small', { text: g.sub }))));
  $('goal-note').textContent = home.goal_note || '';
  const news = $('news-link');
  news.replaceChildren();
  if (home.news) {
    news.append(el('span', {}, el('b', { text: home.news.title + ' ' }), home.news.text), el('span', { 'aria-hidden': 'true', text: '›' }));
    news.onclick = (e) => { e.preventDefault(); show('news'); };
  }
  const play = $('play');
  play.disabled = !!state.running;
  play.querySelector('span').textContent = state.running ? t('RUNNING') : t('PLAY');
}

async function applyGoal(id) {
  const result = await api.apply_goal(id);
  await refresh();
  toast(result && result.message);
}

// ---- settings pages, from the Python side's description ---------------------------------------
async function buildPage(id) {
  let schema;
  try {
    schema = await api.page(id);
    if (!schema || typeof schema !== 'object') throw new Error(String(schema));
  } catch (e) {  // a page that fails says so instead of staying blank (issn 11-06)
    schema = { title: t('Error'), subtitle: '', sections: [{ title: '', rows: [
      { type: 'card', tone: 'bad', title: t('This page could not be loaded. Send a print of it on the Discord.'),
        text: String((e && (e.message || e)) || e) }] }] };
  }
  const old = $('page-' + id);
  const scroll = old ? old.scrollTop : 0;
  const root = el('section', { class: 'page' + (old && old.classList.contains('on') ? ' on still' : ''), id: 'page-' + id, 'aria-label': schema.title });
  const body = el('div', { class: 'settings' }, el('h1', { text: schema.title }), el('p', { class: 'subtitle', text: schema.subtitle }));
  for (const section of schema.sections || []) body.append(renderSection(section));
  if (schema.placeholder) {
    body.append(el('div', { class: 'placeholder' }, el('p', { text: schema.placeholder }),
      el('button', { type: 'button', class: 'btn primary', text: t('Open the classic launcher'), onclick: () => api.classic() })));
  }
  root.append(body);
  if (old) old.replaceWith(root); else $('pages').append(root);
  root.scrollTop = scroll;
}

function renderSection(section) {
  const box = el('div', { class: 'section' + (section.title ? '' : ' plain') });
  if (section.title) box.append(el('h2', { text: section.title }));
  for (const row of section.rows) box.append(renderRow(row));
  return box;
}

function renderRow(row) {
  if (row.type === 'themes') {
    return el('div', { class: 'themes' }, ...state.themes.map((th) =>
      el('button', { type: 'button', class: 'theme' + (th.id === state.theme ? ' on' : ''), onclick: () => setTheme(th.id) },
        el('div', { class: 'swatch', style: `background:${th.hero}` }, el('i', { style: `background:${th.accent};box-shadow:0 0 16px ${th.accent}` }),
          el('b', { style: `background:${th.accent2}` })),
        el('div', { class: 'name' }, th.name, el('small', { text: th.desc })))));
  }
  const special = renderSpecial(row);
  if (special) return special;
  const label = el('div', { class: 'label' }, row.label, row.restart ? el('span', { class: 'restart', text: t('restart') }) : null);
  let control;
  const change = async (value) => {
    if (row.store === 'patch' || row.store === 'game_patch') { await handle((await api.set_choice(row.store, row.key, value)) || {}); return; }
    const result = await api.set_value(row.key, row.store, value);
    if (row.key === 'launcher_fx') document.body.dataset.fx = value;
    await refresh(false);
    await handle({ ...(result || {}), reload: (result && result.reload) || row.reload });
  };
  if (row.type === 'choice') {
    control = el('select', { 'aria-label': row.label, onchange: (e) => change(e.target.value) },
      ...row.options.map(([value, text]) => el('option', { value, text, selected: value === row.value })));
  } else if (row.type === 'segments') {
    control = el('div', { class: 'segments', role: 'group', 'aria-label': row.label }, ...row.options.map(([value, text]) =>
      el('button', { type: 'button', class: 'segment' + (value === row.value ? ' on' : ''), onclick: (e) => {
        e.currentTarget.parentNode.querySelectorAll('.segment').forEach((b) => b.classList.remove('on'));
        e.currentTarget.classList.add('on'); change(value);
      } }, text)));
  } else if (row.type === 'check') {
    const sw = el('span', { class: 'switch' + (row.value ? ' on' : '') }, el('span'));
    control = el('button', { type: 'button', class: 'toggle', 'aria-label': row.label, 'aria-pressed': row.value ? 'true' : 'false',
      onclick: (e) => { const on = e.currentTarget.getAttribute('aria-pressed') !== 'true'; e.currentTarget.setAttribute('aria-pressed', on ? 'true' : 'false'); sw.classList.toggle('on', on); change(on); } }, sw);
  } else if (row.type === 'folder') {
    const input = el('input', { type: 'text', value: row.value || '', 'aria-label': row.label, onchange: (e) => change(e.target.value) });
    control = el('div', { class: 'folder' }, input,
      el('button', { type: 'button', class: 'btn', text: t('Browse…'), onclick: async () => {
        const chosen = await api.choose_folder(row.key, input.value);
        if (chosen) { input.value = chosen; change(chosen); }
      } }));
  } else if (row.type === 'number') {
    control = el('input', { type: 'number', min: row.min, max: row.max, step: row.step, value: row.value, 'aria-label': row.label,
      onchange: (e) => change(e.target.value) });
  } else {
    control = el('span');
  }
  return el('div', { class: 'row' }, label, control, row.hint ? el('div', { class: 'hint', text: row.hint }) : null);
}

// Rows that are not a label and a control: notes, cards, button rows, lists.
function renderSpecial(row) {
  switch (row.type) {
    case 'note': return el('p', { class: 'note', text: row.text });
    case 'buttons': return el('div', { class: 'row wide' }, buttonRow(row.buttons), row.hint ? el('div', { class: 'hint', text: row.hint }) : null);
    case 'card': return renderCard(row);
    case 'cards': return el('div', { class: 'card-grid' }, ...row.cards.map(renderCard));
    case 'progress': return el('div', { class: 'row wide' }, el('div', { class: 'label', text: row.text }),
      el('div', { class: 'bar' }, el('span', { style: `width:${Math.max(0, Math.min(100, row.value))}%` })));
    case 'pick': {
      const select = el('select', { 'aria-label': row.label }, ...row.options.map((o) => el('option', { value: o, text: o })));
      return el('div', { class: 'row' }, el('div', { class: 'label', text: row.label }),
        el('div', { class: 'folder' }, select, buttonRow(row.buttons, () => select.value)),
        row.hint ? el('div', { class: 'hint', text: row.hint }) : null);
    }
    case 'entry': {
      const list = row.suggestions && row.suggestions.length ? 'list-' + row.id : null;
      const input = el('input', { type: row.secret ? 'password' : 'text', 'aria-label': row.label, list, autocomplete: 'off' });
      return el('div', { class: 'row' }, el('div', { class: 'label', text: row.label }),
        el('div', { class: 'folder' }, input, buttonRow(row.buttons, () => input.value),
          list ? el('datalist', { id: list }, ...row.suggestions.map((v) => el('option', { value: v }))) : null),
        row.hint ? el('div', { class: 'hint', text: row.hint }) : null);
    }
    case 'trophies': return el('div', { class: 'trophies' }, ...row.items.map((it) =>
      el('div', { class: 'trophy' + (it.got ? ' got' : '') },
        it.icon ? el('img', { src: it.icon, alt: '' }) : el('span', { class: 'trophy-blank' }),
        el('div', { class: 'trophy-text' }, el('b', { text: it.name }), it.detail ? el('span', { text: it.detail }) : null,
          el('small', { text: it.when })),
        it.grade ? el('span', { class: 'grade', style: it.got ? `color:${it.color}` : '', text: it.grade }) : null)));
    case 'controls': return renderControls(row);
    case 'mods': return el('div', { class: 'row wide' }, el('div', { class: 'mods' }, ...row.items.map((m, i) =>
      el('div', { class: 'mod' },
        el('button', { type: 'button', class: 'btn small', text: '▲', 'aria-label': 'up', disabled: i === 0, onclick: async () => handle(await api.action('mod_move', [m.name, -1])) }),
        el('button', { type: 'button', class: 'btn small', text: '▼', 'aria-label': 'down', disabled: i === row.items.length - 1, onclick: async () => handle(await api.action('mod_move', [m.name, 1])) }),
        el('label', { class: 'mod-name' }, el('input', { type: 'checkbox', checked: m.on, onchange: async (e) => handle(await api.action('mod_toggle', [m.name, e.target.checked])) }),
          `${i + 1}.  ${m.name}`)))),
      row.hint ? el('div', { class: 'hint', text: row.hint }) : null, row.warning ? el('pre', { class: 'warning', text: row.warning }) : null);
    case 'log': return el('div', { class: 'row wide' }, buttonRow(row.buttons), el('pre', { class: 'log', text: row.text || '—' }));
    default: return null;
  }
}

function renderCard(c) {
  return el('div', { class: 'info ' + (c.tone || '') },
    el('div', { class: 'info-head' }, el('b', { text: c.title }), c.badge ? el('span', { class: 'badge', text: c.badge }) : null,
      c.date ? el('small', { text: c.date }) : null),
    c.image ? el('img', { class: 'info-image', src: c.image, alt: '' }) : null,
    c.text ? el('p', { text: c.text }) : null,
    c.mono ? el('pre', { text: c.mono }) : null,
    c.steps && c.steps.length ? el('ol', {}, ...c.steps.map((st) => el('li', { text: st }))) : null,
    c.buttons && c.buttons.length ? buttonRow(c.buttons) : null);
}

function renderControls(row) {
  const head = el('div', { class: 'ctl head' }, el('span', { text: t('GAME BUTTON') }), el('span', { text: t('KEYBOARD') }), el('span', { text: t('GAMEPAD') }));
  return el('div', { class: 'controls' }, head, ...row.rows.map((r) => el('div', { class: 'ctl' },
    el('span', { class: 'ctl-name', text: r.name }),
    el('input', { type: 'text', value: r.key, placeholder: r.default_key, 'aria-label': r.name,
      onchange: (e) => api.set_control('key', r.button, e.target.value) }),
    r.pad_options ? el('select', { 'aria-label': r.name, onchange: (e) => api.set_control('pad', r.button, e.target.value) },
      ...r.pad_options.map(([v, label]) => el('option', { value: v, text: label, selected: v === r.pad })))
      : el('span', { class: 'muted', text: r.fixed }))));
}

async function pressPlay() {
  const result = await api.play();
  await handle(result);
}

async function setTheme(id) {
  state.theme = id;
  document.body.dataset.theme = id;
  await api.set_value('launcher_theme', 'app', id);
  document.querySelectorAll('.theme').forEach((b, i) => b.classList.toggle('on', state.themes[i].id === id));
  await refresh(false);
}

// ---- the whole state ---------------------------------------------------------------------------
async function refresh(full = true) {
  state = { ...(state || {}), ...(await api.state()) };
  T = state.text || {};
  document.body.dataset.theme = state.theme;
  document.body.dataset.fx = state.fx;
  if (full) {
    document.querySelectorAll('[data-t]').forEach((n) => { n.textContent = t(n.dataset.t.replace(/&#10;/g, '\n')); });
    $('version').textContent = 'v' + state.version;
    renderNav();
  }
  renderHome();
}

async function poll() {
  try {
    const s = await api.status();
    $('status').textContent = s.text || '';
    const running = !!s.running;
    document.body.classList.toggle('idle', running);
    if (running !== !!state.running) { state.running = running; renderHome(); }
    if (s.seq !== undefined && s.seq !== poll.seq) {
      const first = poll.seq === undefined;
      poll.seq = s.seq;
      if (!first) { await refresh(false); if (page !== 'home' && !document.querySelector('.modal')) await buildPage(page); }
    }
    if (s.goto) await show(s.goto);
  } catch (e) { /* the window is closing */ }
  setTimeout(poll, 1000);
}

let started = false;
async function start() {
  if (started) return;
  started = true;
  api = window.pywebview ? window.pywebview.api : SAMPLE_API;
  await refresh();
  if (state.font) {
    const face = new FontFace('Bebas Neue', `url(${state.font})`);
    try { document.fonts.add(await face.load()); } catch (e) { /* the system's face stays */ }
  }
  $('expert-switch').onclick = () => setExpert(!state.expert);
  $('discord').onclick = () => api.action('discord');
  $('classic').onclick = () => api.classic();
  $('play').onclick = pressPlay;
  show('home');
  poll();
}

// ---- sample data (a plain browser) -------------------------------------------------------------
const SAMPLE_THEMES = [
  { id: 'smoke', name: 'Fumaça', desc: 'Vermelho, brasa e fumaça', hero: 'linear-gradient(120deg,#2b0d10,#241918 55%,#3a1a0c)', accent: '#e2263b', accent2: '#ff8a3d' },
  { id: 'neon', name: 'Neon', desc: 'Rosa e ciano, luz correndo', hero: 'linear-gradient(120deg,#2a0f2e,#151a3a 55%,#0b2a36)', accent: '#ff3ea5', accent2: '#24d3f2' },
  { id: 'video', name: 'Vídeo', desc: 'Azul, falhas e linhas de TV', hero: 'linear-gradient(120deg,#091a3a,#0c2340 55%,#14123a)', accent: '#3d8bff', accent2: '#7cf0ff' },
  { id: 'concrete', name: 'Concreto', desc: 'Cinza, laranja e poeira', hero: 'linear-gradient(120deg,#2b2a27,#3a3631 55%,#2a221a)', accent: '#ff8a1f', accent2: '#e8dccb' },
];
const SAMPLE = {
  version: '0.3.8', theme: new URLSearchParams(location.search).get('theme') || 'neon', fx: 'full', expert: false, font: '',
  text: { 'Advanced view': 'Modo avançado', 'Classic launcher': 'Launcher clássico', PLAY: 'JOGAR', RUNNING: 'RODANDO', 'Ready check': 'Verificação',
    'Your goal': 'Seu objetivo', THEME: 'TEMA', restart: 'reiniciar', 'Browse…': 'Procurar…', 'Open the classic launcher': 'Abrir o launcher clássico',
    "In the game: Insert or R3+L2\nopens the port's menu.": 'No jogo: Insert ou R3+L2\nabre o menu do port.' },
  pages: [['home', 'Jogar'], ['picture', 'Imagem e desempenho'], ['controls', 'Controles'], ['game', 'Jogo e saves'], ['trophies', 'Conquistas'],
    ['news', 'Novidades'], ['customize', 'Personalizar'], ['help', 'Ajuda']].map(([id, title]) => ({ id, title }))
    .concat([['graphics', 'Gráficos avançados'], ['files', 'Patches e mods'], ['pc', 'Seu PC'], ['advanced', 'Avançado']].map(([id, title]) => ({ id, title, expert: true }))),
  themes: SAMPLE_THEMES,
  home: {
    game: 'second_son', games: [{ id: 'second_son', label: 'inFAMOUS Second Son' }, { id: 'first_light', label: 'inFAMOUS First Light' }],
    art: '', chip: 'CUSA00223 · versão 01.07', sub: '[o save mais recente]', goal: 'recommended', goal_note: 'Esperado no seu PC: cerca de 95 FPS na cidade a 1080p.',
    news: { title: 'Novo na 0.3.7:', text: 'a cidade preta acende sozinha' },
    checks: [
      { state: 'ok', name: 'Placa de vídeo', text: 'Radeon RX 9060 XT, 16 GB', page: 'pc' },
      { state: 'ok', name: 'Jogo', text: 'CUSA00223, versão 01.07', page: 'game' },
      { state: 'ok', name: 'Shaders', text: 'prontos', page: 'graphics' },
      { state: 'ok', name: 'Saves', text: 'backup de hoje', page: 'game' },
      { state: 'ok', name: 'Conquistas', text: '[liberadas] de 48', page: 'trophies' },
      { state: 'warn', name: 'OptiScaler', text: 'não instalado (opcional)', page: 'graphics' },
    ],
    goals: [
      { id: 'recommended', label: 'Recomendado', sub: 'o melhor na maioria dos PCs' }, { id: 'handheld', label: 'Steam Deck / PC fraco', sub: '40-45 FPS' },
      { id: 'smooth', label: 'Fluido', sub: 'até 120 FPS' }, { id: 'balanced', label: 'Equilibrado', sub: '60 FPS, mais nítido' },
    ],
  },
};
const SAMPLE_API = {
  state: async () => SAMPLE, status: async () => ({ text: 'Pronto para jogar.', running: false }),
  set_value: async (key, store, value) => { if (key === 'launcher_theme') SAMPLE.theme = value; if (key === 'expert_view') SAMPLE.expert = value; if (key === 'launcher_fx') SAMPLE.fx = value; return {}; },
  apply_goal: async (id) => { SAMPLE.home.goal = id; return { message: 'Ajustado.' }; },
  page: async (id) => id === 'customize' ? {
    title: 'Personalizar', subtitle: 'O visual do launcher, o idioma e o que ele faz enquanto você joga.',
    sections: [{ title: 'Tema', rows: [{ type: 'themes' }] },
      { title: 'Animação', rows: [{ type: 'segments', key: 'launcher_fx', store: 'app', label: 'Efeitos animados', value: SAMPLE.fx,
        options: [['full', 'Completos'], ['light', 'Leves'], ['off', 'Desligados']], hint: 'Leves e Desligados gastam menos do PC.' }] }],
  } : { title: (SAMPLE.pages.find((p) => p.id === id) || {}).title || id, subtitle: '', sections: [], placeholder: 'Esta página ainda está no launcher clássico.' },
  choose_folder: async () => '', action: async () => ({}), play: async () => ({ message: 'Abrindo o jogo…' }), classic: async () => {},
  set_choice: async () => ({}), set_control: async () => ({}),
};

// pywebview announces its api with this event; a plain browser never does.
if (window.pywebview && window.pywebview.api && window.pywebview.api.state) start();
else window.addEventListener('pywebviewready', start);
setTimeout(() => { if (!window.pywebview) start(); }, 400);
