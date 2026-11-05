'use strict';

const CHAR_LIMIT = 3000;  // LinkedIn post limit
const FOLD_CHARS = 210;   // roughly where the feed cuts to "…see more"
const FOLD_LINES = 3;
const STATUS_LABEL = { draft: 'Draft', ready: 'Ready', posted: 'Posted' };

const state = { posts: [], media: [], current: null, expanded: false, saveTimer: null, saving: null };
const $ = (id) => document.getElementById(id);

// ---------- small utilities ----------
function pref(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (_) { /* storage unavailable: fine */ }
  return null;
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).error || msg; } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}
const jsonOpts = (method, body) => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

let toastTimer;
function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 2200);
}

const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const countChars = (s) => Array.from(s).length;

function normalizeHashtags(raw) {
  return raw.split(/[\s,]+/).map((t) => t.replace(/^#+/, '')).filter(Boolean).map((t) => `#${t}`).join(' ');
}

function composeText(p) {
  const body = (p.body || '').trimEnd();
  const tags = (p.hashtags || '').trim();
  return tags ? `${body}\n\n${tags}` : body;
}

function fold(text) {
  const chars = Array.from(text);
  let cut = Math.min(FOLD_CHARS, chars.length);
  let lines = 0;
  for (let i = 0; i < cut; i++) {
    if (chars[i] === '\n' && ++lines === FOLD_LINES) { cut = i; break; }
  }
  return { shown: chars.slice(0, cut).join('').trimEnd(), truncated: cut < chars.length };
}

const mediaExists = (name) => state.media.some((m) => m.name === name);
const mediaUrl = (name) => `/media/${encodeURIComponent(name)}`;

// ---------- loading ----------
async function load() {
  try {
    const [posts, media] = await Promise.all([api('/api/posts'), api('/api/media')]);
    state.posts = posts;
    state.media = media;
  } catch (err) {
    toast(`Could not reach the server: ${err.message}`);
    return;
  }
  const last = pref('jarvis.li.current');
  const pick = state.posts.find((p) => p.id === last) || sortedPosts()[0];
  if (pick) selectPost(pick.id); else showEmpty();
}

function sortedPosts() {
  return [...state.posts].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

function showEmpty() {
  state.current = null;
  $('editor').hidden = true;
  $('empty').hidden = false;
  renderList();
  renderPreview();
}

// ---------- rendering ----------
function renderList() {
  const q = $('search').value.trim().toLowerCase();
  const list = $('post-list');
  list.replaceChildren();
  for (const p of sortedPosts()) {
    if (q && !`${p.title} ${p.body} ${p.hashtags}`.toLowerCase().includes(q)) continue;
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `post-item${state.current && p.id === state.current.id ? ' active' : ''}`;
    const title = document.createElement('span');
    title.className = 'post-item-title';
    title.textContent = p.title || 'Untitled post';
    const pill = document.createElement('span');
    pill.className = `pill pill-${p.status}`;
    pill.textContent = STATUS_LABEL[p.status] || p.status;
    const meta = document.createElement('span');
    meta.className = 'post-item-meta';
    meta.textContent = `${countChars(composeText(p))} chars · ${p.media.length} photo${p.media.length === 1 ? '' : 's'}`;
    btn.append(title, pill, meta);
    btn.addEventListener('click', () => selectPost(p.id));
    li.append(btn);
    list.append(li);
  }
}

function renderStats() {
  const p = state.current;
  if (!p) return;
  const text = composeText(p);
  const chars = countChars(text);
  const words = (text.match(/\S+/g) || []).length;
  const tags = (p.hashtags.match(/#[\p{L}\p{N}_]+/gu) || []).length;
  const hook = countChars(fold(text).shown);
  const pct = Math.min(100, (chars / CHAR_LIMIT) * 100);
  const notes = [];
  if (chars > CHAR_LIMIT) notes.push(`<span class="bad">${chars - CHAR_LIMIT} characters over LinkedIn's ${CHAR_LIMIT} limit.</span>`);
  if (tags > 5) notes.push('<span class="warn">More than 5 hashtags can look spammy.</span>');
  if (tags === 0) notes.push('<span class="warn">No hashtags yet.</span>');
  if (p.media.length && p.media.some((m) => !mediaExists(m))) notes.push('<span class="warn">Some selected photos are missing from the media folder.</span>');
  $('stats').innerHTML = `
    <div class="meter${chars > CHAR_LIMIT ? ' over' : ''}"><span style="width:${pct}%"></span></div>
    <div class="stat-line">
      <span><b>${chars}</b> / ${CHAR_LIMIT} chars</span>
      <span><b>${words}</b> words · ~${Math.max(1, Math.round(words / 200))} min read</span>
      <span><b>${hook}</b> chars before "see more"</span>
      <span><b>${tags}</b> hashtag${tags === 1 ? '' : 's'}</span>
    </div>
    ${notes.length ? `<div class="stat-line">${notes.join(' ')}</div>` : '<div class="stat-line"><span class="good">Looks good to post.</span></div>'}`;
}

function renderMedia() {
  const grid = $('media-grid');
  grid.replaceChildren();
  const p = state.current;
  if (!state.media.length) {
    const empty = document.createElement('p');
    empty.className = 'media-empty';
    empty.textContent = 'No photos yet. Drop some above. They stay in linkedin_manager/media on your machine.';
    grid.append(empty);
    return;
  }
  for (const m of [...state.media].reverse()) {
    const idx = p ? p.media.indexOf(m.name) : -1;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `media-item${idx >= 0 ? ' selected' : ''}`;
    btn.title = m.name;
    btn.setAttribute('aria-pressed', String(idx >= 0));
    const img = document.createElement('img');
    img.src = mediaUrl(m.name);
    img.alt = m.name;
    img.loading = 'lazy';
    btn.append(img);
    if (idx >= 0) {
      const badge = document.createElement('span');
      badge.className = 'order';
      badge.textContent = String(idx + 1);
      btn.append(badge);
    }
    btn.addEventListener('click', () => toggleMedia(m.name));
    grid.append(btn);
  }
}

function renderPreview() {
  const p = state.current;
  const textEl = $('pv-text');
  const mediaEl = $('pv-media');
  textEl.replaceChildren();
  mediaEl.replaceChildren();
  mediaEl.className = 'pv-media';
  if (!p) return;

  const text = composeText(p);
  const { shown, truncated } = fold(text);
  const visible = state.expanded || !truncated ? text : shown;
  const html = escapeHtml(visible).replace(/(^|\s)(#[\p{L}\p{N}_]+)/gu, '$1<span class="tag">$2</span>');
  textEl.innerHTML = html;
  if (truncated) {
    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'see-more';
    more.textContent = state.expanded ? ' …see less' : '…see more';
    more.addEventListener('click', () => { state.expanded = !state.expanded; renderPreview(); });
    textEl.append(state.expanded ? '\n' : ' ', more);
  }

  const imgs = p.media.filter(mediaExists);
  if (imgs.length) {
    mediaEl.classList.add(`n${Math.min(imgs.length, 4)}`);
    imgs.slice(0, 4).forEach((name, i) => {
      const box = document.createElement('div');
      box.className = 'pv-img';
      const img = document.createElement('img');
      img.src = mediaUrl(name);
      img.alt = '';
      box.append(img);
      if (i === 3 && imgs.length > 4) {
        const more = document.createElement('span');
        more.className = 'pv-more';
        more.textContent = `+${imgs.length - 4}`;
        box.append(more);
      }
      mediaEl.append(box);
    });
  }
}

function renderAvatar() {
  const name = $('pv-name').value.trim();
  $('pv-avatar').textContent = (name[0] || 'Y').toUpperCase();
}

function renderAll() {
  renderList();
  renderStats();
  renderMedia();
  renderPreview();
}

// ---------- editing ----------
function fillForm(p) {
  $('f-title').value = p.title;
  $('f-status').value = p.status;
  $('f-body').value = p.body;
  $('f-hashtags').value = p.hashtags;
  $('f-notes').value = p.notes;
}

async function selectPost(id) {
  await flushSave();
  const p = state.posts.find((x) => x.id === id);
  if (!p) return;
  state.current = p;
  state.expanded = false;
  pref('jarvis.li.current', id);
  $('empty').hidden = true;
  $('editor').hidden = false;
  fillForm(p);
  renderAll();
}

function onFieldInput(field, value) {
  if (!state.current) return;
  state.current[field] = value;
  scheduleSave();
  renderList();
  renderStats();
  renderPreview();
}

function setSaveState(text) { $('save-state').textContent = text; }

function scheduleSave() {
  setSaveState('Unsaved changes…');
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(saveNow, 600);
}

async function saveNow() {
  clearTimeout(state.saveTimer);
  state.saveTimer = null;
  const p = state.current;
  if (!p) return;
  const payload = { title: p.title, body: p.body, hashtags: p.hashtags, status: p.status, notes: p.notes, media: p.media };
  setSaveState('Saving…');
  state.saving = api(`/api/posts/${p.id}`, jsonOpts('PUT', payload))
    .then((saved) => {
      // keep local edits made while the request was in flight
      p.updated_at = saved.updated_at;
      p.posted_at = saved.posted_at;
      setSaveState(state.saveTimer ? 'Unsaved changes…' : 'All changes saved');
    })
    .catch((err) => { setSaveState('Save failed'); toast(`Save failed: ${err.message}`); });
  await state.saving;
  state.saving = null;
}

async function flushSave() {
  if (state.saveTimer) await saveNow();
  else if (state.saving) await state.saving;
}

function toggleMedia(name) {
  const p = state.current;
  if (!p) return;
  const i = p.media.indexOf(name);
  if (i >= 0) p.media.splice(i, 1); else p.media.push(name);
  scheduleSave();
  renderAll();
}

async function createPost(payload = {}) {
  await flushSave();
  try {
    const post = await api('/api/posts', jsonOpts('POST', payload));
    state.posts.push(post);
    await selectPost(post.id);
    $('f-title').focus();
    $('f-title').select();
  } catch (err) { toast(`Could not create post: ${err.message}`); }
}

async function deleteCurrent() {
  const p = state.current;
  if (!p || !confirm(`Delete "${p.title || 'Untitled post'}"? This cannot be undone.`)) return;
  clearTimeout(state.saveTimer);
  state.saveTimer = null;
  try {
    await api(`/api/posts/${p.id}`, { method: 'DELETE' });
    state.posts = state.posts.filter((x) => x.id !== p.id);
    state.current = null;
    const next = sortedPosts()[0];
    if (next) await selectPost(next.id); else showEmpty();
    setSaveState('');
    toast('Post deleted');
  } catch (err) { toast(`Delete failed: ${err.message}`); }
}

async function uploadFiles(files) {
  const list = [...files].filter((f) => f.type.startsWith('image/') || /\.heic$/i.test(f.name));
  if (!list.length) return;
  toast(`Uploading ${list.length} photo${list.length === 1 ? '' : 's'}…`);
  const added = [];
  for (const file of list) {
    try {
      const res = await api(`/api/media?name=${encodeURIComponent(file.name)}`, { method: 'POST', body: file });
      added.push(res.name);
    } catch (err) { toast(`${file.name}: ${err.message}`); }
  }
  state.media = await api('/api/media');
  if (state.current && added.length) {
    state.current.media.push(...added);
    scheduleSave();
  }
  renderAll();
  if (added.length) toast(`Added ${added.length} photo${added.length === 1 ? '' : 's'}`);
}

async function copyText() {
  if (!state.current) return;
  const text = composeText(state.current);
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.append(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
  toast('Post text copied. Paste it into LinkedIn.');
}

// ---------- wiring ----------
function wire() {
  $('f-title').addEventListener('input', (e) => onFieldInput('title', e.target.value));
  $('f-body').addEventListener('input', (e) => onFieldInput('body', e.target.value));
  $('f-notes').addEventListener('input', (e) => onFieldInput('notes', e.target.value));
  $('f-hashtags').addEventListener('input', (e) => onFieldInput('hashtags', e.target.value));
  $('f-hashtags').addEventListener('blur', (e) => {
    const norm = normalizeHashtags(e.target.value);
    if (norm !== e.target.value) { e.target.value = norm; onFieldInput('hashtags', norm); }
  });
  $('f-status').addEventListener('change', (e) => onFieldInput('status', e.target.value));

  $('search').addEventListener('input', renderList);
  $('new-post').addEventListener('click', () => createPost());
  document.querySelector('[data-action="new"]').addEventListener('click', () => createPost());
  $('duplicate').addEventListener('click', () => {
    const p = state.current;
    if (p) createPost({ title: `${p.title} (copy)`, body: p.body, hashtags: p.hashtags, notes: p.notes, media: [...p.media] });
  });
  $('delete').addEventListener('click', deleteCurrent);
  $('copy').addEventListener('click', copyText);
  $('mark-posted').addEventListener('click', () => {
    if (!state.current) return;
    $('f-status').value = 'posted';
    onFieldInput('status', 'posted');
    toast('Marked as posted');
  });

  $('file-input').addEventListener('change', (e) => { uploadFiles(e.target.files); e.target.value = ''; });
  const dz = $('dropzone');
  ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('over'); }));
  dz.addEventListener('drop', (e) => uploadFiles(e.dataTransfer.files));

  for (const [id, key, fallback] of [['pv-name', 'jarvis.li.name', 'Your Name'], ['pv-headline', 'jarvis.li.headline', 'Your headline']]) {
    const input = $(id);
    input.value = pref(key) || fallback;
    input.addEventListener('input', () => { pref(key, input.value); renderAvatar(); });
  }
  renderAvatar();

  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); saveNow(); }
  });
  window.addEventListener('beforeunload', (e) => {
    if (state.saveTimer) { saveNow(); e.preventDefault(); e.returnValue = ''; }
  });
}

wire();
load();
