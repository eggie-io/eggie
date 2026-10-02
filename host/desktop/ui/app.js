'use strict';

// The one global Python reaches, and the one place a pushed event lands.
// Handlers register by event kind; an unknown kind is ignored rather than
// thrown, so an older UI paired with a newer bridge degrades quietly.
window.omelet = {
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

// #notice lives outside #screen, so wire() -- which only ever runs against
// the freshly-swapped-in screen root -- never reaches it. Wired once, here,
// since the script tag runs after the body has parsed.
document.getElementById('notice')
  .querySelector('[data-action="dismiss-notice"]')
  .addEventListener('click', () => ACTIONS['dismiss-notice']());

ACTIONS['check-updates'] = async () => {
  const result = await api().check_app_update();
  show('updates', Object.assign({ none: result.available ? '' : 'yes' }, result));
};

ACTIONS['app-update'] = async () => {
  const started = await api().start_app_update();
  if (started.ok === false) return refresh();
  show('app-update:running', { available: started.version });
};

window.omelet.handlers.app_update = (event) => {
  // The launch-time check usually lands after Home has drawn. Only Home is
  // redrawn: refreshing elsewhere would throw the user off their screen.
  if (event.type === 'available') {
    const screen = document.getElementById('screen').dataset.screen || '';
    if (screen.startsWith('home:')) refresh();
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
    li.innerHTML = '<span class="mark"></span><span class="label"></span><span class="state"></span>';
    li.querySelector('.label').textContent = row.label;
    li.querySelector('.state').textContent = 'Waiting';
    list.appendChild(li);
    rowsByName[row.name] = li;
  });
  // "Step N of M" comes from the list the factory returned, never a literal:
  // seven on Lima, nine on WSL2.
  window.omelet.total = started.rows.length;
  window.omelet.done = 0;
};

ACTIONS['reboot-now'] = () => api().reboot_now();

const STATE_WORDS = {
  running: 'Working…', done: 'Done', skipped: 'Done',
  failed: "Didn't work", reboot: 'Needs restart',
};

window.omelet.handlers.install = (event) => {
  if (event.type === 'step') {
    const li = rowsByName[event.step];
    if (li) {
      li.dataset.status = event.status;
      li.querySelector('.state').textContent = STATE_WORDS[event.status] || '';
    }
    // The overall bar and "Step N of M" move together, on completed steps
    // only -- not on every 'running' tick, and clamped so the last step's
    // 'done' can never read "Step 8 of 7" before the terminal 'done' event
    // (which follows immediately) swaps the screen away.
    if (event.status === 'done' || event.status === 'skipped') {
      window.omelet.done += 1;
      const counter = document.querySelector('[data-field="counter"]');
      if (counter) {
        const step = Math.min(window.omelet.done + 1, window.omelet.total);
        counter.textContent = `Step ${step} of ${window.omelet.total}`;
      }
      const bar = document.querySelector('[data-field="progress"]');
      if (bar) {
        bar.style.width = `${Math.round((window.omelet.done / window.omelet.total) * 100)}%`;
      }
    }
    // fraction is non-null only on the download step: a separate footer line
    // for it, never the overall bar, or the bar would sit full through the
    // several minutes create_vm/bootstrap/connect/verify/finish still take.
    const note = document.querySelector('[data-field="download"]');
    if (note) {
      if (event.fraction !== null && event.fraction !== undefined) {
        const label = li ? li.querySelector('.label').textContent : '';
        note.textContent = `${label} · ${Math.round(event.fraction * 100)}%`;
        note.hidden = false;
      } else {
        note.hidden = true;
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
    swap('install:failed', { action: event.action || event.message });
    // DeadEnd means no code can fix it; a retry button there loops forever.
    if (event.type === 'dead_end') {
      document.querySelector('[data-retry]')?.remove();
    }
    return;
  }
};

// Swap the left column without rebuilding the row panel, so the rows keep the
// states the stream already put on them. rowsByName is rebuilt from the
// spliced-in clone (keyed by the data-name each <li> carries) rather than
// left pointing at the pre-clone elements, so a later 'step' event still
// finds its row instead of silently no-oping.
function swap(screen, data) {
  const rows = document.querySelector('[data-rows]');
  const keep = rows ? rows.cloneNode(true) : null;
  show(screen, data);
  if (keep) {
    document.querySelector('[data-rows]').replaceWith(keep);
    rowsByName = {};
    keep.querySelectorAll('.row-step').forEach((li) => {
      rowsByName[li.dataset.name] = li;
    });
  }
}

ACTIONS['start-over'] = async () => { await api().reset_install(); refresh(); };

// --- Settings and quit (also reached from the tray) -------------------

ACTIONS['settings'] = async () => {
  const settings = await api().get_settings();
  show('settings', { unavailable: settings.autostart_available ? '' : 'yes', error: '' });
  const box = document.querySelector('[data-setting="autostart"]');
  box.checked = settings.autostart;
  box.disabled = !settings.autostart_available;
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

// A tray route lands while a job's screen (install, update, import) may be
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

window.omelet.route = (name) => {
  const go = ROUTES[name];
  if (go) go();
};

// --- Import ---------------------------------------------------------

// Set by choose-folder, read by do-import, cleared when Import is opened --
// otherwise a user who opens Import and clicks "Bring it in" without
// choosing a folder would re-import whatever they picked last time.
let pending = null;

ACTIONS['import'] = () => {
  // Reopening Import must not re-import whatever was chosen last time:
  // do-import reads pending, and the screen alone doesn't show a stale path.
  pending = null;
  show('import', { path: '', name: '', summary: '' });
};

ACTIONS['choose-folder'] = async () => {
  const chosen = await api().choose_folder();
  if (chosen.cancelled) return;
  pending = chosen;
  const megabytes = (chosen.bytes / 1e6).toFixed(1);
  fill(document.getElementById('screen'), {
    path: chosen.path, name: chosen.name,
    summary: `${chosen.files} files · ${megabytes} MB`,
  });
  document.querySelector('[data-conflict]').hidden = !chosen.conflict;
};

ACTIONS['do-import'] = async () => {
  if (!pending) return;
  // No conflict means there is nothing to merge into or replace, so the
  // radios are hidden and merge is the only meaning.
  const picked = document.querySelector('input[name="mode"]:checked');
  const mode = pending.conflict && picked ? picked.value : 'merge';
  const started = await api().start_import(pending.path, mode);
  show('import:progress', { name: started.name, counter: '' });
};

window.omelet.handlers.import = (event) => {
  if (event.type === 'progress') {
    const percent = event.total ? Math.round((event.done / event.total) * 100) : 0;
    const bar = document.querySelector('[data-field="fraction"]');
    if (bar) bar.style.width = `${percent}%`;
    const counter = document.querySelector('[data-field="counter"]');
    if (counter) {
      counter.textContent = event.phase === 'packing'
        ? `Packing ${event.done} of ${event.total} files`
        : `${percent}% copied`;
    }
    return;
  }
  if (event.type === 'crashed') showNotice(event.message);
  if (event.type === 'done' || event.type === 'crashed') refresh();
};

// --- Doctor, repair, VM lifecycle, uninstall --------------------------

ACTIONS['doctor'] = async () => show('doctor', await api().doctor());
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

window.omelet.handlers.vm = (event) => {
  if (event.type === 'progress') return;
  if (event.type === 'crashed') showNotice(event.message);
  refresh();
};
window.omelet.handlers.background_start = () => {
  const screen = document.getElementById('screen').dataset.screen || '';
  if (screen.startsWith('home:') || screen === 'unreachable' || screen === 'unresponsive') refresh();
};
// Opening from the tray: a page drawn while hidden may be stale. Screens
// holding user input or a job are left alone.
window.omelet.handlers.window = (event) => {
  if (event.type !== 'shown') return;
  const screen = document.getElementById('screen').dataset.screen || '';
  if (screen.startsWith('home:') || screen === 'unreachable' || screen === 'unresponsive') refresh();
};
window.omelet.handlers.repair = (event) => {
  if (event.type === 'progress') return;
  // 'stage' events (bootstrap, connect) mark progress mid-repair, not the
  // end of the job -- refreshing on one re-renders the screen as though
  // repair did nothing, and a second click then rejects on JobBusy.
  if (event.type === 'stage') return;
  if (event.type === 'crashed') showNotice(event.message);
  refresh();
};
// Auto-started once per session: an update that "succeeds" without fixing the
// API would otherwise restart itself on every refresh.
let runtimeUpdateTried = false;
// home.problem is empty when the runtime answers but speaks an old API, so the
// failed screen reached again from refresh() shows the job's own error.
let runtimeUpdateError = '';

async function startRuntimeUpdate() {
  runtimeUpdateTried = true;
  show('runtime-update:running', {});
  await api().start_runtime_update();
}

ACTIONS['retry-runtime-update'] = () => startRuntimeUpdate();
JOB_ACTIONS.add('retry-runtime-update');

window.omelet.handlers.runtime_update = (event) => {
  if (event.type === 'progress' || event.type === 'stage') return;
  if (event.type === 'crashed') {
    runtimeUpdateError = event.message;
    return show('runtime-update:failed', { message: event.message });
  }
  refresh();
};

window.omelet.handlers.recover = (event) => {
  // The wider restart stops more than this machine, so it is offered, never
  // taken; if even that fails, the probe lands back on the unresponsive screen.
  if (event.type === 'unresponsive' && !event.everything) return show('recover-confirm', event);
  if (event.type === 'unresponsive' || event.type === 'crashed') showNotice(event.message);
  refresh();
};
window.omelet.handlers.uninstall = (event) => {
  if (event.type === 'crashed') showNotice(event.message);
  refresh();
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
  document.title = 'Omelet';
  // A window RunOnce reopened by itself must continue setup, not show Home.
  if (home.resumed) return ACTIONS['start-install'](null, home);
  if (home.first_run) return show('first-run', home);
  if (home.enter_console) {
    const result = await api().enter_console();
    if (result.ok) return window.location.assign(result.url);
    showNotice(result.message);
  }
  if (home.route === 'update_runtime') {
    if (!runtimeUpdateTried) return startRuntimeUpdate();
    return show('runtime-update:failed', { message: runtimeUpdateError || home.problem });
  }
  show(home.route === 'home' ? `home:${home.state}` : home.route, home);
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
