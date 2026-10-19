// Long-running Isracard and Bank Hapoalim fetcher: reads credentials once from stdin (decrypted by systemd-creds into the
// unit's private memory and piped in), keeps them only in this process, fetches daily at FETCH_AT
// (Asia/Jerusalem) and on request. The HTTP port is reachable only on the internal compose network.
// Bank Hapoalim may ask for an SMS code: a run started from the app then waits for the code typed there
// (POST /code); a scheduled run never waits, and Hapoalim stays out of scheduled runs until a login on the
// remembered device has succeeded, so nobody gets an SMS at 06:00 they did not ask for.
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { join } from 'node:path';
import { text } from 'node:stream/consumers';
import { SOURCES, fetchToInbox, pruneInbox, validateCredentials } from './lib.mjs';

const outDir = process.env.FETCH_OUT ?? '/out';
const monthsBack = Number(process.env.FETCH_MONTHS_BACK ?? '1');
const fetchAt = process.env.FETCH_AT ?? '06:00';
const keepDays = Number(process.env.FETCH_KEEP_DAYS ?? '45');
const stateDir = process.env.FETCH_STATE ?? '/state';
const CODE_WAIT_MS = 5 * 60 * 1000;
const NEEDS_CODE = new Set(['hapoalim']);
if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(fetchAt)) throw new Error('FETCH_AT must be HH:MM');

let credentials;
try {
  credentials = validateCredentials(JSON.parse(await text(process.stdin)));
} catch (error) {
  console.error(`error: no usable credentials on stdin: ${error.message}`);
  process.exit(1);
}

const state = { running: false, last: null, next: null, waiting: null };

// {source: {scheduled: bool}}: whether a scheduled run may log in without anyone at the phone.
function loadSources() {
  try {
    return JSON.parse(readFileSync(join(stateDir, 'sources.json'), 'utf8'));
  } catch {
    return {};
  }
}
function saveSources(value) {
  mkdirSync(stateDir, { recursive: true, mode: 0o700 });
  writeFileSync(join(stateDir, 'sources.json.tmp'), JSON.stringify(value), { mode: 0o600 });
  renameSync(join(stateDir, 'sources.json.tmp'), join(stateDir, 'sources.json'));
}
const scheduledAllowed = (source) => !NEEDS_CODE.has(source) || loadSources()[source]?.scheduled === true;
function setScheduled(source, value) {
  if (NEEDS_CODE.has(source)) saveSources({ ...loadSources(), [source]: { scheduled: value } });
}

// Called by the Hapoalim login when the bank shows its code field; resolves with the code from POST /code.
function requestCode(source) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      state.waiting = null;
      reject(new Error('no SMS code was entered within 5 minutes'));
    }, CODE_WAIT_MS);
    state.waiting = {
      source,
      until: new Date(Date.now() + CODE_WAIT_MS).toISOString(),
      resolve: (code) => {
        clearTimeout(timer);
        state.waiting = null;
        resolve(code);
      },
    };
  });
}

async function run(trigger) {
  if (state.running) return false;
  state.running = true;
  const started = new Date();
  try {
    // One source failing (an expired password, a site change) does not stop the others.
    const accounts = [];
    const errors = {};
    const skipped = [];
    for (const source of Object.keys(SOURCES).filter((name) => credentials[name])) {
      if (trigger === 'schedule' && !scheduledAllowed(source)) {
        skipped.push(source);
        continue;
      }
      const hooks = trigger === 'schedule' ? { requestCode: null } : { requestCode: () => requestCode(source) };
      try {
        accounts.push(...(await fetchToInbox(source, credentials[source], { outDir, monthsBack, stateDir, hooks })));
        setScheduled(source, true); // logged in on this profile; next time the bank should know the device
      } catch (error) {
        console.error(`error: ${source}: ${error.message}`);
        errors[source] = String(error.message).slice(0, 300);
        if (error.needsCode && trigger === 'schedule') setScheduled(source, false);
      } finally {
        state.waiting = null;
      }
    }
    const failed = Object.keys(errors);
    state.last = {
      at: started.toISOString(),
      finished_at: new Date().toISOString(),
      ok: !failed.length,
      trigger,
      accounts,
      errors,
      skipped,
      ...(failed.length ? { error: failed.map((name) => errors[name]).join('; ') } : {}),
    };
  } finally {
    state.running = false;
    try {
      const removed = pruneInbox(outDir, keepDays);
      if (removed) console.log(`pruned ${removed} fetched files older than ${keepDays} days`);
    } catch (error) {
      console.error(`error: pruning inbox: ${error.message}`);
    }
  }
  return true;
}

function schedule() {
  // TZ=Asia/Jerusalem in the container, so local time is Israeli time.
  const [hours, minutes] = fetchAt.split(':').map(Number);
  const next = new Date();
  next.setHours(hours, minutes, 0, 0);
  if (next <= new Date()) next.setDate(next.getDate() + 1);
  state.next = next.toISOString();
  setTimeout(async () => {
    await run('schedule');
    schedule();
  }, next - new Date());
}

const server = createServer(async (request, response) => {
  const reply = (status, body) => {
    response.writeHead(status, { 'content-type': 'application/json' });
    response.end(JSON.stringify(body));
  };
  if (request.method === 'GET' && request.url === '/status') {
    const waiting = state.waiting && { source: state.waiting.source, until: state.waiting.until };
    const manualOnly = Object.keys(credentials).filter((source) => !scheduledAllowed(source));
    return reply(200, { running: state.running, waiting_for_code: waiting, manual_only: manualOnly, last: state.last, next_run: state.next, schedule: fetchAt, months_back: monthsBack, sources: Object.keys(credentials) });
  }
  if (request.method === 'POST' && request.url === '/code') {
    let code;
    try {
      code = String(JSON.parse((await text(request)).slice(0, 200)).code ?? '');
    } catch {
      return reply(400, { accepted: false, reason: 'expected JSON {"code": "..."}' });
    }
    if (!/^\d{4,8}$/.test(code)) return reply(400, { accepted: false, reason: 'the code is 4-8 digits' });
    if (!state.waiting) return reply(409, { accepted: false, reason: 'no login is waiting for a code' });
    state.waiting.resolve(code);
    return reply(202, { accepted: true });
  }
  if (request.method === 'POST' && request.url === '/run') {
    if (state.running) return reply(409, { started: false, reason: 'already running' });
    run('manual');
    return reply(202, { started: true });
  }
  return reply(404, { error: 'not found' });
});
server.listen(8080, () => console.log(`fetcher ready (${Object.keys(credentials).join(', ')}); daily at ${fetchAt} Asia/Jerusalem, ${monthsBack} month(s) back`));
schedule();
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => process.exit(0));
