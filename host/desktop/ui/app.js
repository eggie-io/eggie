'use strict';

// The one global Python reaches, and the one place a pushed event lands.
// Handlers register by event kind; an unknown kind is ignored rather than
// thrown, so an older UI paired with a newer bridge degrades quietly.
window.eggie = {
  handlers: {},
  on(event) {
    const handler = this.handlers[event.kind];
    if (handler) handler(event);
  },
};

const api = () => window.pywebview.api;

// Bumped on every screen change; a refresh that waited on home() while the
// screen changed must not draw over it.
let screenGeneration = 0;

function show(screen, data) {
  screenGeneration += 1;
  const template = document.querySelector(`template[data-screen="${screen}"]`);
  if (!template) throw new Error(`no template for ${screen}`);
  const root = document.getElementById('screen');
  clearBusy();
  if (!KEEPS_STASH.has(screen)) stash = null;
  root.replaceChildren(template.content.cloneNode(true));
  root.dataset.screen = screen;
  fill(root, data || {});
  wire(root);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Every [data-field] gets its key as textContent, never markup: versions and
// error text are data, some of it from a subprocess.
function fill(root, data) {
  root.querySelectorAll('[data-field]').forEach((node) => {
    const value = data[node.dataset.field];
    if (value !== undefined && value !== null) node.textContent = String(value);
  });
  // A section that only makes sense when its field has a value -- the runtime
  // version is empty on every machine where the runtime never installed, and
  // its separator would otherwise render as a trailing " · ".
  root.querySelectorAll('[data-when]').forEach((node) => {
    node.hidden = !data[node.dataset.when];
  });
}

// These resolve as soon as the job starts; the screen only changes when the
// job's terminal event calls refresh(), so they stay busy until show().
const JOB_ACTIONS = new Set(['start-vm', 'stop-vm', 'restart-vm', 'repair', 'do-uninstall',
  'recover', 'recover-everything']);

function wire(root) {
  root.querySelectorAll('[data-action]').forEach((node) => {
    node.addEventListener('click', () => {
      const name = node.dataset.action;
      const result = ACTIONS[name](node);
      if (!result || typeof result.then !== 'function') return;
      setBusy(node);
      result.then(
        () => { if (!JOB_ACTIONS.has(name)) clearBusy(); },
        // Rethrown so the unhandledrejection notice still explains it.
        (error) => { clearBusy(); throw error; },
      );
    });
  });
}

let busy = { timer: null, node: null };

function setBusy(node) {
  clearBusy();
  // Disabled at once so a second click can't hit JobBusy; the spinner waits
  // so a fast call doesn't flash one.
  if (node) node.disabled = true;
  busy = {
    node,
    timer: setTimeout(() => {
      document.getElementById('busy').hidden = false;
      if (node) node.setAttribute('aria-busy', 'true');
    }, 150),
  };
}

function clearBusy() {
  clearTimeout(busy.timer);
  document.getElementById('busy').hidden = true;
  if (busy.node) {
    busy.node.disabled = false;
    busy.node.removeAttribute('aria-busy');
  }
  busy = { timer: null, node: null };
}

// #notice sits outside #screen (a sibling in the body, not inside any
// <template>) so it survives show()'s wholesale replaceChildren -- a crash
// bouncing the user Home must not also erase the reason it happened.
function showNotice(message) {
  // textContent: this is exception text from a subprocess inside the VM.
  document.getElementById('notice-detail').textContent = String(message || '');
  document.getElementById('notice').hidden = false;
}

// Every bridge call is a promise. A rejected one -- JobBusy from a double
// click, or a provider that threw -- would otherwise make the button do
// nothing at all, with nothing on screen to explain it.
window.addEventListener('unhandledrejection', (event) => {
  showNotice(event.reason && event.reason.message ? event.reason.message : event.reason);
});

const ACTIONS = {
  'enter-console': async () => {
    const result = await api().enter_console();
    if (result.ok) window.location.assign(result.url);
    else showNotice(result.message);
  },
  'go-home': () => refresh(),
  'dismiss-notice': () => { document.getElementById('notice').hidden = true; },
};

// #notice lives outside #screen, so show()'s wire() -- which only ever runs
// against the freshly-swapped-in screen root -- never reaches it. Wired once,
// here, since the script tag runs after the body has parsed.
wire(document.getElementById('notice'));

ACTIONS['check-updates'] = async () => {
  const result = await api().check_app_update();
  show('updates', Object.assign({ none: result.available ? '' : 'yes' }, result));
};

ACTIONS['app-update'] = async () => {
  const started = await api().start_app_update();
  if (started.ok === false) return refresh();
  show('app-update:running', { available: started.version });
};

window.eggie.handlers.app_update = (event) => {
  // The launch-time check usually lands after Home has drawn. Only Home is
  // redrawn: refreshing elsewhere would throw the user off their screen.
  if (event.type === 'available') {
    const screen = document.getElementById('screen').dataset.screen || '';
    if (screen === 'home') refresh();
    return;
  }
  if (event.type === 'progress') {
    const bar = document.querySelector('[data-field="fraction"]');
    if (bar && event.total) bar.style.width = `${Math.round((event.done / event.total) * 100)}%`;
    return;
  }
  // 'done' needs nothing: the window is closing.
  if (event.type === 'crashed') { showNotice(event.message); refresh(); }
};

// Rows the install screen fills from start_install()'s row list, keyed by
// step name so a pushed 'step' event can find its <li> again. Rebuilt only
// when a fresh install starts, never on a screen swap.
let rowsByName = {};

ACTIONS['start-install'] = async (node, data) => {
  const started = await api().start_install();
  rowsByName = {};
  show('install:running', Object.assign({ counter: '' }, data));
  const list = document.querySelector('[data-rows]');
  started.rows.forEach((row) => {
    const li = document.createElement('li');
    li.className = 'row-step';
    li.dataset.status = 'waiting';
    li.dataset.name = row.name;
    const progress = el('div', 'row-progress');
    progress.hidden = true;
    const track = el('span', 'track');
    track.append(el('span', 'fill'));
    progress.append(track, el('span', 'pct'));
    li.append(el('span', 'mark'), el('span', 'label', row.label), el('span', 'state', 'Waiting'),
      progress);
    list.appendChild(li);
    rowsByName[row.name] = li;
  });
  // "Step N of M" comes from the list the factory returned, never a literal:
  // seven on Lima, nine on WSL2.
  window.eggie.total = started.rows.length;
  window.eggie.done = 0;
};

ACTIONS['reboot-now'] = () => api().reboot_now();
ACTIONS['open-vm-sign-in-page'] = () => api().open_vm_sign_in_page();

const STATE_WORDS = {
  running: 'Working…', done: 'Done', skipped: 'Done',
  failed: "Didn't work", reboot: 'Needs restart',
};

window.eggie.handlers.install = (event) => {
  if (event.type === 'step') {
    const li = rowsByName[event.step];
    if (li) {
      li.dataset.status = event.status;
      li.querySelector('.state').textContent = (event.status === 'running' && event.message)
        || STATE_WORDS[event.status] || '';
      li.querySelector('.row-reopen')?.remove();
      if (event.status === 'running' && event.reopen) {
        const reopen = el('button', 'btn-ghost btn-sm row-reopen', 'Open the page again');
        reopen.dataset.action = 'open-vm-sign-in-page';
        li.append(reopen);
        wire(li);
      }
    }
    // The overall bar and "Step N of M" move together, on completed steps
    // only -- not on every 'running' tick, and clamped so the last step's
    // 'done' can never read "Step 8 of 7" before the terminal 'done' event
    // (which follows immediately) swaps the screen away.
    if (event.status === 'done' || event.status === 'skipped') {
      window.eggie.done += 1;
      const counter = document.querySelector('[data-field="counter"]');
      if (counter) {
        const step = Math.min(window.eggie.done + 1, window.eggie.total);
        counter.textContent = `Step ${step} of ${window.eggie.total}`;
      }
      const bar = document.querySelector('[data-field="progress"]');
      if (bar) {
        bar.style.width = `${Math.round((window.eggie.done / window.eggie.total) * 100)}%`;
      }
    }
    // fraction is non-null only on the download step: it fills that row's own
    // bar, never the overall bar, or the bar would sit full through the
    // several minutes create_vm/bootstrap/connect/verify/finish still take.
    const download = li && li.querySelector('.row-progress');
    if (download) {
      if (typeof event.fraction === 'number') {
        const pct = `${Math.round(event.fraction * 100)}%`;
        download.querySelector('.fill').style.width = pct;
        download.querySelector('.pct').textContent = pct;
        download.hidden = false;
      } else {
        download.hidden = true;
      }
    }
    return;
  }
  if (event.type === 'done') return showPicker('local');
  if (event.type === 'reboot') return swap('install:reboot');
  // A worker exception ('crashed', from jobs.py) is otherwise unhandled and
  // would leave the screen spinning forever with no way out but quitting.
  // Unlike dead_end, a crash may well succeed on retry, so the button stays.
  if (event.type === 'failed' || event.type === 'dead_end' || event.type === 'crashed') {
    swap('install:failed', { action: event.action || event.message, message: event.message });
    // The cause is at the end of the guest's output, after pages of pulls.
    const log = document.querySelector('.install-failed details.log');
    log?.addEventListener('toggle', () => {
      const pre = log.querySelector('pre');
      pre.scrollTop = pre.scrollHeight;
    });
    // DeadEnd means no code can fix it; a retry button there loops forever.
    if (event.type === 'dead_end') {
      document.querySelector('[data-retry]')?.remove();
    }
    return;
  }
};

// Swap the header without rebuilding the row panel, so the rows keep the
// states the stream already put on them. rowsByName is rebuilt from the
// spliced-in clone (keyed by the data-name each <li> carries) rather than
// left pointing at the pre-clone elements, so a later 'step' event still
// finds its row instead of silently no-oping. The counter and overall bar
// only move on completed steps, so they are carried over too.
function swap(screen, data) {
  const rows = document.querySelector('[data-rows]');
  const keep = rows ? rows.cloneNode(true) : null;
  const counter = document.querySelector('[data-field="counter"]');
  const bar = document.querySelector('[data-field="progress"]');
  const counterText = counter ? counter.textContent : '';
  const barWidth = bar ? bar.style.width : '';
  show(screen, data);
  if (keep) {
    document.querySelector('[data-rows]').replaceWith(keep);
    wire(keep);
    rowsByName = {};
    keep.querySelectorAll('.row-step').forEach((li) => {
      rowsByName[li.dataset.name] = li;
    });
  }
  const newCounter = document.querySelector('[data-field="counter"]');
  if (newCounter) newCounter.textContent = counterText;
  const newBar = document.querySelector('[data-field="progress"]');
  if (newBar) newBar.style.width = barWidth;
}

ACTIONS['start-over'] = async () => { await api().reset_install(); refresh(); };

// --- Settings and quit (also reached from the tray) -------------------

ACTIONS['settings'] = async () => {
  const settings = await api().get_settings();
  show('settings', {
    unavailable: settings.autostart_available ? '' : 'yes', error: '',
    signed_in: settings.signed_in, email: settings.email, vm_exists: settings.vm_exists,
    switch_back: settings.switch_back,
    no_email: settings.signed_in && !settings.email,
  });
  const box = document.querySelector('[data-setting="autostart"]');
  box.checked = settings.autostart;
  box.disabled = !settings.autostart_available;
};

function askSignOut(asking) {
  document.querySelector('[data-sign-out]').hidden = !asking;
  document.querySelector('[data-action="sign-out-ask"]').disabled = asking;
}
ACTIONS['sign-out-ask'] = () => askSignOut(true);
ACTIONS['sign-out-cancel'] = () => askSignOut(false);
ACTIONS['sign-out'] = async () => {
  await api().sign_out();
  signIn = null;
  return refresh();
};

ACTIONS['toggle-autostart'] = async (node) => {
  let error = '';
  try {
    const result = await api().set_autostart(node.checked);
    if (!result.ok) error = result.error;
  } finally {
    // Re-read rather than trust the click: the OS has the final say, and a
    // rejected call must not leave the box in the clicked state.
    try {
      node.checked = (await api().get_settings()).autostart;
    } catch (_) {
      node.checked = !node.checked;
    }
    document.querySelector('[data-field="error"]').textContent = error;
  }
};

// A tray route lands while a job's screen (install, update) may be
// showing; its nodes are parked, not discarded, so the job's live handlers
// keep a DOM to update and Cancel/Back can put it back.
let stash = null;
// The screens a tray route shows over the parked one; any other show()
// means the parked screen is stale.
const KEEPS_STASH = new Set(['settings', 'quit-confirm', 'quitting']);

function stashScreen() {
  const root = document.getElementById('screen');
  const screen = root.dataset.screen;
  if (!screen || stash || screen === 'settings' || screen === 'quit-confirm') return;
  screenGeneration += 1;
  const nodes = document.createDocumentFragment();
  nodes.append(...root.childNodes);
  stash = { nodes, screen };
}

function restoreScreen() {
  if (!stash) return false;
  const root = document.getElementById('screen');
  clearBusy();
  root.replaceChildren(stash.nodes);
  root.dataset.screen = stash.screen;
  stash = null;
  return true;
}

async function startQuit(force) {
  const result = await api().quit(force);
  if (result.confirm) return show('quit-confirm', {});
  show('quitting', {});
}

ACTIONS['quit-anyway'] = () => startQuit(true);
ACTIONS['quit-cancel'] = () => { if (!restoreScreen()) return refresh(); };
ACTIONS['settings-back'] = () => { if (!restoreScreen()) return refresh(); };

// stashScreen() leaves #screen empty until the route draws; a rejected bridge
// call must put the parked screen back (the rejection still reaches #notice).
async function fromTray(go) {
  stashScreen();
  try {
    return await go();
  } catch (error) {
    if (!restoreScreen() && !document.getElementById('screen').hasChildNodes()) refresh();
    throw error;
  }
}

const ROUTES = {
  'settings': () => fromTray(ACTIONS.settings),
  'quit': () => fromTray(() => startQuit(false)),
};

window.eggie.route = (name) => {
  const go = ROUTES[name];
  if (go) go();
};

// --- Doctor, repair, VM lifecycle, uninstall --------------------------

ACTIONS['doctor'] = async () => show('doctor', await api().doctor());

// Returns nothing so wire() doesn't put the busy spinner over a copy.
function copyFrom(node, source) {
  const idle = node.dataset.idle || (node.dataset.idle = node.textContent);
  const label = (text) => {
    node.textContent = text;
    clearTimeout(node.resetTimer);
    node.resetTimer = setTimeout(() => { node.textContent = idle; }, 2500);
  };
  // execCommand is the fallback for a webview that refuses the async
  // clipboard; if both fail the text stays selected for a keyboard copy.
  const fallback = () => {
    const range = document.createRange();
    range.selectNodeContents(source);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    let copied = false;
    try { copied = document.execCommand('copy'); } catch (error) { copied = false; }
    label(copied ? 'Copied' : 'Selected \u2014 copy it with your keyboard');
  };
  const text = source.textContent;
  if (!navigator.clipboard) return fallback();
  navigator.clipboard.writeText(text).then(() => label('Copied'), fallback);
}
ACTIONS['copy-log'] = (node) => {
  copyFrom(node, node.closest('details.log, section, #notice').querySelector('pre'));
};
ACTIONS['copy-details'] = (node) => {
  copyFrom(node, node.closest('.kcard').querySelector('[data-copy-source]'));
};
ACTIONS['repair'] = async () => { await api().start_repair(); };
ACTIONS['start-vm'] = async () => { await api().start_vm(); };
ACTIONS['stop-vm'] = async () => { await api().stop_vm(); };
ACTIONS['restart-vm'] = async () => { await api().restart_vm(); };
ACTIONS['recover'] = async () => { await api().recover_vm(false); };
ACTIONS['recover-everything'] = async () => { await api().recover_vm(true); };

ACTIONS['uninstall'] = () => show('uninstall-confirm', {});
ACTIONS['do-uninstall'] = async () => {
  const purge = document.querySelector('input[name="purge"]').checked;
  await api().start_uninstall(purge);
};

window.eggie.handlers.vm = (event) => {
  if (event.type === 'progress') return;
  if (event.type === 'crashed') showNotice(event.message);
  refresh();
};
window.eggie.handlers.background_start = () => {
  const screen = document.getElementById('screen').dataset.screen || '';
  if (screen === 'home' || screen === 'unreachable' || screen === 'unresponsive') refresh();
};
// Opening from the tray: a page drawn while hidden may be stale. Screens
// holding user input or a job are left alone.
window.eggie.handlers.window = (event) => {
  if (event.type !== 'shown') return;
  const screen = document.getElementById('screen').dataset.screen || '';
  if (screen === 'home' || screen === 'unreachable' || screen === 'unresponsive') refresh();
};
window.eggie.handlers.repair = (event) => {
  if (event.type === 'progress') return;
  // 'stage' events (bootstrap, connect) mark progress mid-repair, not the
  // end of the job -- refreshing on one re-renders the screen as though
  // repair did nothing, and a second click then rejects on JobBusy.
  if (event.type === 'stage') return;
  if (event.type === 'crashed') showNotice(event.message);
  refresh();
};
async function startRuntimeUpdate() {
  show('runtime-update:running', {});
  await api().start_runtime_update();
}

ACTIONS['retry-runtime-update'] = () => startRuntimeUpdate();
JOB_ACTIONS.add('retry-runtime-update');

window.eggie.handlers.runtime_update = (event) => {
  if (event.type === 'progress' || event.type === 'stage') return;
  if (event.type === 'crashed') return show('runtime-update:failed', { message: event.message });
  refresh();
};

window.eggie.handlers.recover = (event) => {
  // The wider restart stops more than this machine, so it is offered, never
  // taken; if even that fails, the probe lands back on the unresponsive screen.
  if (event.type === 'unresponsive' && !event.everything) return show('recover-confirm', event);
  if (event.type === 'unresponsive' || event.type === 'crashed') showNotice(event.message);
  refresh();
};
window.eggie.handlers.uninstall = (event) => {
  if (event.type === 'crashed') showNotice(event.message);
  refresh();
};

// --- The Eggie account (1a, 1b) and where to cook (1c) ------------------

// The code the window is waiting on, and which button asked for it: "Open
// the page again" and the QR code reopen that same address.
let signIn = null;

const SIGN_IN_ENDED = {
  expired: 'That code ran out before it was approved.',
  denied: 'The sign-in was denied in the browser.',
};

function showAccount(reason) {
  show('account', { reason });
  api().account_screen_shown();
}

async function beginSignIn(mode, openPage) {
  const generation = screenGeneration;
  const started = await api().start_sign_in(mode);
  if (generation !== screenGeneration) return;
  if (!started.ok) return showAccount(started.message);
  signIn = {
    mode, url: started.url, code: started.user_code,
    until: Date.now() + started.expires_in * 1000,
  };
  if (openPage) await api().open_sign_in_page();
  if (generation !== screenGeneration) return;
  showWaiting();
}

function minutesLeft() {
  const minutes = Math.max(1, Math.ceil((signIn.until - Date.now()) / 60000));
  return minutes === 1 ? '1 more minute' : `${minutes} more minutes`;
}

function showWaiting() {
  show('account:waiting', {
    user_code: signIn.code, minutes: minutesLeft(),
    create: signIn.mode === 'create', existing: signIn.mode !== 'create',
  });
  drawQr(document.querySelector('[data-qr]'), signIn.url);
  whileShowing(15000, () => {
    document.querySelector('[data-field="minutes"]').textContent = minutesLeft();
  });
}

// Whole modules only, or the code blurs on a fractional device pixel ratio.
function drawQr(canvas, text) {
  const code = qrcode(0, 'M');
  code.addData(text);
  code.make();
  const modules = code.getModuleCount();
  const quiet = 2;
  const cells = modules + quiet * 2;
  const ratio = window.devicePixelRatio || 1;
  const scale = Math.max(1, Math.floor((84 * ratio) / cells));
  const size = cells * scale;
  canvas.width = size;
  canvas.height = size;
  canvas.style.width = `${size / ratio}px`;
  canvas.style.height = `${size / ratio}px`;
  const context = canvas.getContext('2d');
  context.fillStyle = '#ffffff';
  context.fillRect(0, 0, size, size);
  context.fillStyle = '#1d1829';
  for (let row = 0; row < modules; row += 1) {
    for (let col = 0; col < modules; col += 1) {
      if (code.isDark(row, col)) {
        context.fillRect((col + quiet) * scale, (row + quiet) * scale, scale, scale);
      }
    }
  }
}

// Runs `tick` every `ms` until the screen drawn just before this call changes.
function whileShowing(ms, tick) {
  const generation = screenGeneration;
  const timer = setInterval(() => {
    if (generation !== screenGeneration) return clearInterval(timer);
    tick();
  }, ms);
}

ACTIONS['create-account'] = () => beginSignIn('create', true);
ACTIONS['existing-account'] = () => beginSignIn('existing', true);
ACTIONS['open-sign-in-page'] = () => api().open_sign_in_page();
// The code stays pending: pressing either button again reuses it.
ACTIONS['sign-in-back'] = () => showAccount('');

window.eggie.handlers.account = (event) => {
  signIn = null;
  const screen = document.getElementById('screen').dataset.screen || '';
  // Over Settings from the tray: Back must redraw, not restore the old 1b.
  if (screen !== 'account' && screen !== 'account:waiting') {
    stash = null;
    return;
  }
  if (event.type === 'signed_in') return refresh();
  showAccount(SIGN_IN_ENDED[event.type] || '');
};

// --- A cloud kitchen: make it (1d), pay in the browser, watch it (1e) ----

// `payment` is what went to the checkout page: Back from the wait restores it,
// and 1e's "Paid" line reads it.
let kitchenForm = null;
let payment = null;
let setupKitchen = '';

async function newKitchen(draft) {
  const generation = screenGeneration;
  const catalog = await api().catalog();
  if (generation !== screenGeneration) return;
  if (catalog.signed_out) return refresh();
  if (!catalog.ok) return show('kitchen:new', { error: catalog.message, retry: 'yes' });
  kitchenForm = Object.assign({ catalog, name: catalog.name, size: '', region: '' }, draft || {});
  show('kitchen:new', { ready: 'yes' });
  const input = document.querySelector('[data-kitchen-name]');
  input.value = kitchenForm.name;
  input.addEventListener('input', () => { kitchenForm.name = input.value; updateContinue(); });
  buildPicks('[data-sizes]', 'size', catalog.sizes, true);
  const anySize = catalog.sizes.length ? catalog.sizes[0].id : '';
  buildPicks('[data-regions]', 'region', catalog.regions[anySize] || [], false);
  drawPicks();
}

function firstEnabled(tiles, id) {
  const chosen = tiles.find((tile) => tile.id === id && tile.enabled);
  const fallback = tiles.find((tile) => tile.enabled);
  return (chosen || fallback || { id: '' }).id;
}

// Tiles are built once and updated in place, so a radio keeps its focus.
function buildPicks(selector, name, tiles, isSize) {
  const group = document.querySelector(selector);
  tiles.forEach((tile) => {
    const label = document.createElement('label');
    label.className = isSize ? 'pick' : 'pick pick-region';
    label.dataset.id = tile.id;
    const input = document.createElement('input');
    input.type = 'radio';
    input.name = name;
    input.value = tile.id;
    input.addEventListener('change', () => { kitchenForm[name] = tile.id; drawPicks(); });
    const top = document.createElement('span');
    top.className = 'pick-top';
    const mark = document.createElement('span');
    mark.className = isSize ? 'pick-dot' : 'ic ic-globe pick-globe';
    mark.setAttribute('aria-hidden', 'true');
    const title = document.createElement('span');
    title.className = 'pick-label';
    title.textContent = tile.label;
    top.append(mark, title);
    if (isSize) {
      const price = document.createElement('span');
      price.className = 'pick-price';
      price.textContent = tile.price;
      top.append(price);
    } else {
      const check = document.createElement('span');
      check.className = 'ic ic-check-circle pick-check';
      check.setAttribute('aria-hidden', 'true');
      top.append(check);
    }
    label.append(input, top);
    if (isSize && tile.line) {
      const line = document.createElement('span');
      line.className = 'pick-line';
      line.textContent = tile.line;
      label.append(line);
    }
    // A size has a price or a note, never both, so the note takes the price's place.
    const note = document.createElement('span');
    note.className = 'pick-note';
    (isSize ? top : label).append(note);
    group.append(label);
  });
}

function drawPicks() {
  const { catalog } = kitchenForm;
  kitchenForm.size = firstEnabled(catalog.sizes, kitchenForm.size);
  const regions = catalog.regions[kitchenForm.size] || [];
  kitchenForm.region = firstEnabled(regions, kitchenForm.region);
  const paint = (selector, tiles, chosen) => {
    document.querySelectorAll(`${selector} .pick`).forEach((label) => {
      const tile = tiles.find((t) => t.id === label.dataset.id) || { enabled: false, note: '' };
      const input = label.querySelector('input');
      input.disabled = !tile.enabled;
      input.checked = tile.enabled && tile.id === chosen;
      label.querySelector('.pick-note').textContent = tile.note;
    });
  };
  paint('[data-sizes]', catalog.sizes, kitchenForm.size);
  paint('[data-regions]', regions, kitchenForm.region);
  updateContinue();
}

function updateContinue() {
  document.querySelector('[data-action="continue-to-payment"]').disabled = !(
    kitchenForm.size && kitchenForm.region && kitchenForm.name.trim());
}

function showFormError(message) {
  const alert = document.querySelector('.kitchen .alert');
  alert.querySelector('[data-field="error"]').textContent = message;
  alert.querySelector('[data-action]').hidden = true;
  alert.hidden = false;
}

// setTimeout, so each look waits for the last answer and the bridge sets the
// delay. Parked under Settings it pauses instead of ending.
function pollWhileShowing(firstDelay, tick) {
  const node = document.getElementById('screen').firstElementChild;
  const next = (ms) => setTimeout(async () => {
    if (!node.isConnected) {
      if (stash && stash.nodes.contains(node)) next(ms);
      return;
    }
    let delay = ms;
    try {
      delay = await tick(node);
    } catch (error) {
      delay = ms;
    }
    if (delay !== null) next(delay);
  }, ms);
  next(firstDelay);
}

ACTIONS['new-kitchen'] = () => newKitchen(null);
ACTIONS['kitchen-back'] = () => refresh();

ACTIONS['continue-to-payment'] = async () => {
  const draft = { name: kitchenForm.name.trim(), size: kitchenForm.size, region: kitchenForm.region };
  const generation = screenGeneration;
  const started = await api().start_checkout(draft.name, draft.size, draft.region);
  if (generation !== screenGeneration) return;
  if (started.signed_out) return refresh();
  if (!started.ok) return showFormError(started.message);
  const size = kitchenForm.catalog.sizes.find((s) => s.id === draft.size);
  payment = { draft, size: size.label, price: size.money, kitchen: '' };
  waitForPayment();
};

// A cancelled payment or a closed tab looks the same as one still open, so
// this never gives up; Back is the way out.
function waitForPayment() {
  show('kitchen:setup', { waiting: 'yes' });
  pollWhileShowing(3000, async (node) => {
    const status = await api().checkout_status();
    if (!node.isConnected) return 3000;
    if (status.state === 'found') {
      payment.kitchen = status.kitchen_id;
      showSetup(status.kitchen_id);
      return null;
    }
    if (status.state === 'signed_out') {
      refresh();
      return null;
    }
    return status.state === 'waiting' ? status.wait * 1000 : null;
  });
}

ACTIONS['open-checkout-page'] = () => api().open_checkout_page();
ACTIONS['checkout-back'] = async () => {
  await api().cancel_checkout();
  return newKitchen(payment ? payment.draft : null);
};

async function showSetup(kitchenId) {
  const generation = screenGeneration;
  const kitchen = await api().kitchen(kitchenId);
  if (generation !== screenGeneration) return;
  if (!kitchen.ok) {
    if (!kitchen.signed_out) showNotice(kitchen.message);
    return refresh();
  }
  if (kitchen.state === 'running') return showPicker(kitchen.id);
  const paid = payment && payment.kitchen === kitchenId ? payment : {};
  setupKitchen = kitchen.id;
  show('kitchen:setup', {
    setting_up: 'yes', title: kitchen.title, steps_done: kitchen.steps_done,
    where_line: [kitchen.region_label, kitchen.size_label].filter(Boolean).join(' · '),
    paid_size: paid.size || '', paid_price: paid.price || '',
  });
  drawSetup(kitchen);
  pollWhileShowing(3000, async (node) => {
    const next = await api().kitchen(kitchenId);
    if (!node.isConnected) return 3000;
    if (next.signed_out) {
      refresh();
      return null;
    }
    document.querySelector('[data-field="offline"]').textContent = next.ok ? '' : next.message;
    if (!next.ok) return 3000;
    if (next.state === 'running') {
      showPicker(next.id);
      return null;
    }
    if (next.state === 'stopped' || next.state === 'other') {
      refresh();
      return null;
    }
    drawSetup(next);
    return 3000;
  });
}

function drawSetup(kitchen) {
  let current = null;
  document.querySelectorAll('[data-setup-rows] .row-step').forEach((li, i) => {
    const status = kitchen.rows[i];
    li.dataset.status = status;
    li.querySelector('.state').textContent = (
      status === 'running' && kitchen.slow_boot ? 'Can take a minute' : '');
    li.querySelector('.detail')?.remove();
    if (status === 'failed' && kitchen.last_error) {
      const detail = document.createElement('span');
      detail.className = 'detail';
      detail.textContent = kitchen.last_error;
      li.append(detail);
    }
    if (!current && status !== 'done') current = li;
  });
  document.querySelector('[data-setup-current]').textContent = (
    current ? current.querySelector('.label').textContent : '');
  document.querySelector('[data-field="title"]').textContent = kitchen.title;
  document.querySelector('[data-field="steps_done"]').textContent = kitchen.steps_done;
  const bar = document.querySelector('.setup-card .bar');
  bar.setAttribute('aria-valuenow', kitchen.steps_done);
  bar.querySelector('span').style.width = `${(kitchen.steps_done / kitchen.rows.length) * 100}%`;
  document.querySelector('[data-action="open-kitchen-page"]').hidden = kitchen.state !== 'failed';
}

ACTIONS['open-kitchen-page'] = () => api().open_kitchen_page(setupKitchen);

// 1c's and Home's price line; each reads fine without it if the catalog can't load.
// Kept once found, so Home's redraws don't ask the service again.
let fromPrice = '';

async function showFromPrice() {
  const line = document.querySelector('[data-from-price]');
  if (!fromPrice) {
    const catalog = await api().catalog();
    if (catalog.ok) fromPrice = catalog.from_price;
  }
  if (!line.isConnected || !fromPrice) return;
  line.querySelector('[data-field="from_price"]').textContent = fromPrice;
  line.hidden = false;
}

// --- Home (2a–2c): every kitchen as a card -------------------------------

// A card from a <template data-card>, filled and wired before it joins the page.
function card(name, data, kitchenId) {
  const part = document.querySelector(`template[data-card="${name}"]`).content.cloneNode(true);
  part.querySelectorAll('[data-kitchen]').forEach((node) => { node.dataset.kitchen = kitchenId || ''; });
  fill(part, data);
  wire(part);
  return part;
}

function agentChip(name) {
  const chip = document.createElement('span');
  chip.className = 'badge badge-running';
  const icon = document.createElement('span');
  icon.className = 'ic ic-check-circle';
  icon.setAttribute('aria-hidden', 'true');
  const label = document.createElement('span');
  label.textContent = name;
  chip.append(icon, label);
  return chip;
}

// `agents` are the names of the card's connected agents (api.home()).
// With none yet, Connect an agent is the card's main button.
function withAgents(name, data, kitchenId, agents, canConnect) {
  const has = agents.length > 0;
  const part = card(name, Object.assign(data, {
    has_agents: has, no_agents: !has, can_connect: canConnect, agents_row: has || canConnect,
  }), kitchenId);
  part.querySelector('[data-chips]').append(...agents.map(agentChip));
  if (!has) part.querySelector('[data-action="connect-kitchen"]').className = 'btn-primary btn-sm';
  return part;
}

function cloudCard(kitchen) {
  const opensSetup = kitchen.state === 'getting_ready' || kitchen.state === 'failed';
  const data = Object.assign({}, kitchen, {
    [kitchen.state]: true, opens_setup: opensSetup, plain_title: !opensSetup,
    host_line: kitchen.state !== 'getting_ready' && kitchen.line,
  });
  return withAgents('cloud', data, kitchen.id, kitchen.agents || [], kitchen.can_connect);
}

// The full screen a failed local card's Try again opens (view.local_repair), or null.
let localRepair = null;

function localCard(local, home) {
  const failed = local.state === 'failed';
  const detail = local.repair_message || home.problem;
  localRepair = failed && local.repair ? { name: local.repair, detail } : null;
  const data = {
    [local.state]: true, problem: detail, error: failed && detail,
    repair: Boolean(localRepair), restart: failed && !localRepair,
  };
  // This computer can always be picked; 3a says when it can't take an agent yet.
  return withAgents('local', data, 'local', local.agents || [], true);
}

ACTIONS['open-repair'] = () => {
  if (!localRepair) return refresh();
  show(localRepair.name, { problem: localRepair.detail, message: localRepair.detail });
};

function drawHome(home) {
  const view = home.home;
  show('home', Object.assign({}, home, view));
  localRepair = null;
  const list = document.querySelector('[data-cards]');
  view.cards.forEach((kitchen) => {
    list.append(kitchen.kind === 'local' ? localCard(kitchen, home) : cloudCard(kitchen));
  });
  if (view.missing) list.append(card(`add-${view.missing}`, {}));
  if (view.missing === 'cloud') showFromPrice();
  const delay = view.poll_seconds * 1000;
  // A pressed card button is waiting on its job, whose refresh() redraws.
  const pressed = () => busy.node && busy.node.isConnected;
  pollWhileShowing(delay, async (node) => {
    if (pressed()) return delay;
    await api().load_kitchens();
    const next = await api().home();
    if (!node.isConnected) return stash && stash.nodes.contains(node) ? delay : null;
    if (pressed()) return delay;
    if (next.screen !== 'home' || next.action) {
      refresh();
      return null;
    }
    const root = document.getElementById('screen');
    const top = root.scrollTop;
    drawHome(next);
    root.scrollTop = top;
    return null;
  });
}

ACTIONS['retry-kitchens'] = async () => {
  await api().load_kitchens();
  return refresh();
};
ACTIONS['open-cloud-console'] = (node) => api().open_cloud_console(node.dataset.kitchen);
ACTIONS['kitchen-setup'] = (node) => showSetup(node.dataset.kitchen);
ACTIONS['connect-kitchen'] = (node) => showPicker(node.dataset.kitchen);

// A cloud kitchen made on the website meanwhile takes the page to Home.
async function watchForKitchens() {
  const loaded = await api().load_kitchens();
  if (!loaded || !loaded.ok) return;
  const home = await api().home();
  if (document.getElementById('screen').dataset.screen !== 'where') return;
  if (home.screen !== 'where' || home.action) refresh();
}

// --- Connect an agent (3a–3d) -------------------------------------------

// The picker's last agents() reply, the connect job it started, and 3c's guide.
let picker = null;
let confirming = null;
let connecting = null;
let ready = null;
// Events can beat start_connect()'s reply here; held until it names the job.
let connectEarly = [];

const TARGET_STATES = {
  stopped: 'Stopped', getting_ready: 'Getting ready', failed: 'Didn’t start', other: 'Not running',
};

async function showPicker(kitchenId) {
  const generation = screenGeneration;
  const reply = await api().agents(kitchenId);
  if (generation !== screenGeneration) return;
  if (!reply.ok) return refresh();
  picker = reply;
  const { kitchen } = reply;
  show('connect', { kitchen: kitchen.title, not_running: !kitchen.enabled });
  const select = document.querySelector('[data-targets]');
  reply.targets.forEach((target) => {
    const option = el('option', '', target.enabled ? target.title
      : `${target.title} · ${TARGET_STATES[target.state] || TARGET_STATES.other}`);
    option.value = target.id;
    option.disabled = !target.enabled;
    option.selected = target.id === kitchen.id;
    select.append(option);
  });
  select.addEventListener('change', () => showPicker(select.value));
  const list = document.querySelector('[data-agent-cards]');
  reply.cards.forEach((agent) => list.append(agentCard(agent, kitchen)));
}

function agentCard(agent, kitchen) {
  const part = document.querySelector('template[data-card="agent"]').content.cloneNode(true);
  part.querySelectorAll('[data-agent]').forEach((node) => { node.dataset.agent = agent.id; });
  part.querySelector('[data-icon]').src = mediaPath(agent.icon);
  fill(part, Object.assign({}, agent, { [agent.state]: true, kitchen: kitchen.title }));
  part.querySelector('[data-action="pick-agent"]').disabled = !kitchen.enabled;
  wire(part);
  return part;
}

// Paths come from the bridge's checked manifests; anything else loads nothing.
function mediaPath(path) {
  return /^agents\/[a-z0-9][a-z0-9-]*\/[a-z0-9][a-z0-9._/-]*$/.test(path || '') ? path : '';
}

function pickedAgent(node) {
  return picker.cards.find((agent) => agent.id === node.dataset.agent);
}

// A connected card connects again too: the kitchen may have dropped this computer's key.
// One that would switch this computer's default WSL distro asks first.
ACTIONS['pick-agent'] = (node) => {
  const agent = pickedAgent(node);
  return agent.switches ? showConfirm(agent) : startConnect(agent, picker.kitchen);
};

function showConfirm(agent) {
  confirming = agent;
  show('connect:confirm', { agent: agent.name, kitchen: picker.kitchen.title, distro: agent.switches });
  // The guide's own words for this OS, when it has them.
  if (agent.confirm) {
    document.querySelector('[data-confirm-title]').textContent = agent.confirm.title;
    document.querySelector('[data-confirm-button]').textContent = agent.confirm.button;
    document.querySelector('[data-confirm-body]').replaceChildren(...stepBody(agent.confirm.body).childNodes);
  }
}
ACTIONS['connect-confirm'] = () => startConnect(confirming, picker.kitchen, true);
ACTIONS['get-agent'] = (node) => api().open_agent_download(node.dataset.agent || ready.agent.id);

async function startConnect(agent, kitchen, confirmed = false) {
  const started = await api().start_connect(agent.id, kitchen.id, confirmed);
  // The default changed since the picker drew: ask now.
  if (!started.ok && started.switches) {
    return showConfirm(Object.assign({}, agent, { switches: started.switches, confirm: started.confirm }));
  }
  if (!started.ok) return showNotice(started.message);
  connecting = {
    agent, kitchen, confirmed, job: started.job, rows: started.rows,
    status: {}, message: {}, ended: '', error: '', lines: [], signIn: false,
  };
  const early = connectEarly.filter((event) => event.job === started.job);
  connectEarly = [];
  early.forEach(onConnectEvent);
  if (connecting) drawConnecting();
}

window.eggie.handlers.connect = (event) => {
  if (!connecting || connecting.job !== event.job) {
    connectEarly.push(event);
    return;
  }
  onConnectEvent(event);
  const screen = document.getElementById('screen').dataset.screen || '';
  // Parked under Settings or left for another screen: nothing to redraw.
  if (connecting && (screen === 'connect:running' || screen === 'connect:sign-in')) drawConnecting();
};

function onConnectEvent(event) {
  const c = connecting;
  if (event.type === 'step') {
    c.status[event.step] = event.status;
    c.message[event.step] = event.message;
    // 3d stays up once the sign-in began, so a failed one offers Try again there.
    if (event.step === 'agent' && event.status === 'running' && event.message === 'sign_in') c.signIn = true;
    if (event.step === 'agent' && event.status === 'done') c.signIn = false;
    return;
  }
  if (event.type === 'done') {
    const screen = document.getElementById('screen').dataset.screen || '';
    if (screen === 'connect' || screen.startsWith('connect:')) {
      showReady(c.agent, c.kitchen, event.steps, event.switch_back || '');
    }
    else connecting = null;
    return;
  }
  if (event.type === 'signed_out') {
    connecting = null;
    const screen = document.getElementById('screen').dataset.screen || '';
    if (screen === 'connect' || screen.startsWith('connect:')) refresh();
    return;
  }
  if (event.type === 'failed' || event.type === 'crashed') {
    c.ended = 'failed';
    c.error = event.message || '';
    c.lines = event.lines || [];
  }
}

const ROW_WORDS = { running: 'Working…', done: 'Done', failed: 'Didn’t work' };

function drawConnecting() {
  const c = connecting;
  const ssh = c.rows.includes('check');
  const done = c.rows.filter((row) => c.status[row] === 'done').length;
  const common = { agent: c.agent.name, kitchen: c.kitchen.title, total: c.rows.length };
  if (c.signIn) {
    show('connect:sign-in', Object.assign(common, {
      step: c.rows.indexOf('agent') + 1, waiting: !c.ended, error: c.ended ? c.error : '',
    }));
    return;
  }
  show('connect:running', Object.assign(common, {
    done, working: !c.ended, failed: c.ended === 'failed', lines: c.lines.join('\n'),
    cloud: c.kitchen.kind === 'cloud', local: c.kitchen.kind === 'local', ssh, no_ssh: !ssh,
  }));
  let current = null;
  document.querySelectorAll('.connect-rows [data-row]').forEach((li) => {
    const row = li.dataset.row;
    li.hidden = !c.rows.includes(row);
    const status = c.status[row] || 'waiting';
    li.dataset.status = status;
    const waitingOnYou = status === 'running' && c.message[row] === 'approve';
    li.querySelector('.state').textContent = waitingOnYou ? 'Waiting for you in the browser'
      : ROW_WORDS[status] || '';
    if (status === 'failed' && c.error) li.append(el('span', 'detail', c.error));
    if (!li.hidden && !current && status !== 'done') current = li;
  });
  document.querySelector('[data-connect-current]').textContent = current
    ? current.querySelector('.label:not([hidden])').textContent : '';
  const bar = document.querySelector('.connecting .bar');
  bar.setAttribute('aria-valuemax', c.rows.length);
  bar.setAttribute('aria-valuenow', done);
  bar.querySelector('span').style.width = `${(done / c.rows.length) * 100}%`;
}

async function leaveConnecting(then) {
  connecting = null;
  await api().cancel_connect();
  return then();
}

ACTIONS['connect-cancel'] = () => leaveConnecting(() => showPicker(picker.kitchen.id));
ACTIONS['connect-leave'] = () => leaveConnecting(refresh);
ACTIONS['connect-back'] = () => leaveConnecting(() => showPicker(picker.kitchen.id));
ACTIONS['connect-retry'] = () => {
  const { agent, kitchen, confirmed } = connecting;
  connecting = null;
  return startConnect(agent, kitchen, confirmed);
};
ACTIONS['open-connect-page'] = () => api().open_connect_page();

const COUNTS = ['', 'One step', 'Two steps', 'Three steps', 'Four steps', 'Five steps', 'Six steps'];

function showReady(agent, kitchen, steps, switchBack) {
  connecting = null;
  ready = { agent, kitchen, steps };
  show('connect:ready', {
    agent: agent.name, kitchen: kitchen.title, count: COUNTS[steps.length] || `${steps.length} steps`,
    has_app: agent.has_app, missing: !agent.has_app && agent.state === 'missing',
    switch_back: switchBack,
  });
  const list = document.querySelector('[data-ready-steps]');
  steps.forEach((step, i) => {
    const item = el('li', 'ready-step');
    const pick = el('button', 'ready-pick');
    pick.type = 'button';
    pick.append(el('span', 'ready-num', String(i + 1)));
    const text = el('span', 'ready-text');
    text.append(el('span', 'ready-title', step.title), stepBody(step.body));
    pick.append(text);
    pick.addEventListener('click', () => pickStep(i));
    item.append(pick);
    list.append(item);
  });
  const first = steps.findIndex((step) => step.media.length);
  pickStep(first < 0 ? 0 : first);
}

function pickStep(index) {
  const step = ready.steps[index];
  document.querySelectorAll('.ready-step').forEach((item, i) => {
    item.classList.toggle('picked', i === index);
    item.querySelector('button').setAttribute('aria-pressed', String(i === index));
  });
  const pane = document.querySelector('[data-ready-media]');
  const pictures = (step ? step.media : []).map(mediaPath).filter(Boolean);
  pane.replaceChildren(...pictures.map((path) => {
    const img = el('img');
    img.src = path;
    img.alt = step.alt;
    return img;
  }));
  if (!pictures.length) pane.append(el('p', 'ready-placeholder', step ? step.alt : ''));
}

// A guide body is HTML from a checked manifest. It is rebuilt from the
// allowlisted tags only, as new elements: never parsed into the page itself.
const BODY_TAGS = new Set(['P', 'UL', 'OL', 'LI', 'STRONG', 'EM', 'B', 'I', 'CODE', 'KBD', 'BR']);

function stepBody(html) {
  const parsed = new DOMParser().parseFromString(String(html || ''), 'text/html');
  const copy = (from, to) => {
    from.childNodes.forEach((node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        to.append(document.createTextNode(node.textContent));
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        if (!BODY_TAGS.has(node.tagName)) return copy(node, to);
        const built = document.createElement(node.tagName.toLowerCase());
        copy(node, built);
        to.append(built);
      }
    });
  };
  const body = el('div', 'step-body');
  copy(parsed.body, body);
  return body;
}

ACTIONS['open-agent'] = () => api().open_agent(ready.agent.id, ready.kitchen.id);

// From 3c back to the picker, whose card then shows the agent as not connected;
// from Settings, Settings again without the offer.
ACTIONS['switch-default-back'] = async () => {
  const result = await api().switch_default_back();
  if (!result.ok) return showNotice(result.message);
  const screen = document.getElementById('screen').dataset.screen || '';
  if (screen === 'connect:ready') return showPicker(ready.kitchen.id);
  // A parked 3c would still offer the switch back.
  stash = null;
  return ACTIONS.settings();
};
ACTIONS['connect-done'] = async () => {
  await api().mark_done(ready.agent.id, ready.kitchen.id);
  return refresh();
};

async function refresh() {
  stash = null;
  if (!busy.timer) setBusy(null);
  const generation = screenGeneration;
  let home;
  try {
    home = await api().home();
  } catch (error) {
    clearBusy();
    throw error;
  }
  if (generation !== screenGeneration) return;
  document.title = 'Eggie';
  // The bridge decided (view.screen_for); this only carries it out. An
  // action that cannot complete falls through to the screen it named.
  if (home.action === 'start_install') return ACTIONS['start-install'](null, home);
  if (home.action === 'start_runtime_update') return startRuntimeUpdate();
  if (home.action === 'load_kitchens') {
    const loaded = await api().load_kitchens();
    if (generation !== screenGeneration) return;
    if (loaded && loaded.ok) return refresh();
    // Asked once more, not refresh(): that would load again forever. This reply has the error.
    home = await api().home();
    if (generation !== screenGeneration) return;
  }
  if (home.screen === 'account') {
    signIn = null;
    return showAccount('');
  }
  // A reload forgets which button was pressed; the plain sign-in link works for both.
  if (home.screen === 'account:waiting') return beginSignIn(signIn ? signIn.mode : 'existing', false);
  if (home.screen === 'home') return drawHome(home);
  if (home.screen === 'connect') return showPicker(home.kitchen_id);
  show(home.screen, home);
  if (home.screen === 'where') {
    whileShowing(30000, watchForKitchens);
    showFromPrice();
  }
}

function start() {
  const route = window.location.hash.slice(1);
  // The tray reloads this page with #settings or #quit when the console was
  // showing; cleared so a later reload lands on Home.
  if (route && ROUTES[route]) {
    history.replaceState(null, '', window.location.pathname);
    return ROUTES[route]();
  }
  return refresh();
}

window.addEventListener('pywebviewready', start);
