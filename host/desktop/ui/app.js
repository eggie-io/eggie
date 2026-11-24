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

// Every [data-field] is replaced by the matching key. Values are written with
// textContent, never innerHTML: a runtime version or a provider's error text
// is data, and some of it comes from a subprocess.
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
    li.innerHTML = '<span class="mark"></span><span class="label"></span><span class="state"></span>'
      + '<div class="row-progress" hidden><span class="track"><span class="fill"></span></span>'
      + '<span class="pct"></span></div>';
    li.querySelector('.label').textContent = row.label;
    li.querySelector('.state').textContent = 'Waiting';
    list.appendChild(li);
    rowsByName[row.name] = li;
  });
  // "Step N of M" comes from the list the factory returned, never a literal:
  // seven on Lima, nine on WSL2.
  window.eggie.total = started.rows.length;
  window.eggie.done = 0;
};

ACTIONS['reboot-now'] = () => api().reboot_now();

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
  if (event.type === 'done') return refresh();
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
    signed_in: settings.signed_in, email: settings.email,
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

// The console's agent guide sends the window here as
// #connect=<agent>&back=<guide path> after the user clicked "Add Eggie to
// <agent>". The window adds it straight away and goes back: the click in the
// guide is the request. The bridge checks both values again, and what it can
// write is fixed (an entry for this VM), so a page sending the window here
// unasked can do no more than that.
let connectRequest = null;

async function openConnect(agent, back) {
  const offer = await api().agent_connection(agent);
  if (!offer.ok) {
    showNotice(offer.message);
    return refresh();
  }
  connectRequest = { agent, back, offer };
  return ACTIONS['connect-agent']();
}

ACTIONS['connect-agent'] = async () => {
  show('agent-connect', connectRequest.offer);
  const result = await api().connect_agent(connectRequest.agent);
  if (!result.ok) return show('agent-connect:failed', result);
  // The guide shows the result: it opens with the connection marked added.
  return ACTIONS['connect-back']();
};
ACTIONS['connect-back'] = async () => {
  const result = await api().enter_console(connectRequest ? connectRequest.back : '');
  if (result.ok) window.location.assign(result.url);
  else show('agent-connect:failed', result);
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
  if (kitchen.state === 'running') return show('connect', { kitchen_id: kitchen.id });
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
      show('connect', { kitchen_id: next.id });
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

// Chips come from the agent manifests; until those ship every card has none.
// With none yet, Connect an agent is the card's main button.
function withAgents(name, data, kitchenId, agents, canConnect) {
  const has = agents.length > 0;
  const part = card(name, Object.assign(data, {
    has_agents: has, no_agents: !has, can_connect: canConnect, agents_row: has || canConnect,
  }), kitchenId);
  if (!has) part.querySelector('[data-action="connect-kitchen"]').className = 'btn-primary btn-sm';
  return part;
}

function cloudCard(kitchen) {
  const opensSetup = kitchen.state === 'getting_ready' || kitchen.state === 'failed';
  const data = Object.assign({}, kitchen, {
    [kitchen.state]: true, opens_setup: opensSetup, plain_title: !opensSetup,
    host_line: kitchen.state !== 'getting_ready' && kitchen.line,
  });
  return withAgents('cloud', data, kitchen.id, [], kitchen.can_connect);
}

function localCard(local, home) {
  const failed = local.state === 'failed';
  const unresponsive = home.route === 'unresponsive';
  const data = {
    [local.state]: true, problem: home.problem, error: failed && home.problem,
    retry_start: failed && !unresponsive, retry_recover: failed && unresponsive,
  };
  // This computer can always be picked; 3a says when it can't take an agent yet.
  return withAgents('local', data, 'local', [], true);
}

function drawHome(home) {
  const view = home.home;
  show('home', Object.assign({}, home, view));
  const list = document.querySelector('[data-cards]');
  view.cards.forEach((kitchen) => {
    list.append(kitchen.kind === 'local' ? localCard(kitchen, home) : cloudCard(kitchen));
  });
  if (view.missing) list.append(card(`add-${view.missing}`, {}));
  if (view.missing === 'cloud') showFromPrice();
  const delay = view.cards.some((kitchen) => kitchen.state === 'getting_ready') ? 3000 : 30000;
  // A pressed card button is waiting on its job, whose refresh() redraws.
  const pressed = () => busy.node && busy.node.isConnected;
  pollWhileShowing(delay, async (node) => {
    if (pressed()) return delay;
    await api().load_kitchens();
    const next = await api().home();
    if (!node.isConnected) return stash && stash.nodes.contains(node) ? delay : null;
    if (pressed()) return delay;
    if (next.screen === 'home' && !next.action) drawHome(next);
    else refresh();
    return null;
  });
}

ACTIONS['retry-kitchens'] = async () => {
  await api().load_kitchens();
  return refresh();
};
ACTIONS['open-cloud-console'] = (node) => api().open_cloud_console(node.dataset.kitchen);
ACTIONS['kitchen-setup'] = (node) => showSetup(node.dataset.kitchen);
ACTIONS['connect-kitchen'] = (node) => show('connect', { kitchen_id: node.dataset.kitchen });

// A cloud kitchen made on the website meanwhile takes the page to Home.
async function watchForKitchens() {
  const loaded = await api().load_kitchens();
  if (!loaded || !loaded.ok) return;
  const home = await api().home();
  if (document.getElementById('screen').dataset.screen !== 'where') return;
  if (home.screen !== 'where' || home.action) refresh();
}

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
    // A failed load is drawn as it is; refreshing would ask again forever.
    if (loaded && loaded.ok) return refresh();
  }
  if (home.screen === 'account') {
    signIn = null;
    return showAccount('');
  }
  // A reload forgets which button was pressed; the plain sign-in link works for both.
  if (home.screen === 'account:waiting') return beginSignIn(signIn ? signIn.mode : 'existing', false);
  if (home.screen === 'home') return drawHome(home);
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
  const params = new URLSearchParams(route);
  if (params.has('connect')) {
    history.replaceState(null, '', window.location.pathname);
    return openConnect(params.get('connect'), params.get('back') || '');
  }
  return refresh();
}

window.addEventListener('pywebviewready', start);
