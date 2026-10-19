// Fetch Isracard cards and Bank Hapoalim accounts with israeli-bank-scrapers and write one JSON file per
// card or account into the inbox. The files go through the normal import preview; nothing is written to the
// ledger here. Credentials are never logged or written; amounts are not printed either.
// Files are group-readable so the app container (group 10001) can offer them read-only.
import { mkdirSync, readFileSync, readdirSync, renameSync, rmSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { CompanyTypes, createScraper } from 'israeli-bank-scrapers';
import puppeteer from 'puppeteer';
import { HapoalimWithCode } from './hapoalim.mjs';

export const SCRAPER_VERSION = JSON.parse(
  readFileSync(new URL('./node_modules/israeli-bank-scrapers/package.json', import.meta.url), 'utf8'),
).version;
const INBOX_FILE = /^(isracard|hapoalim)-\d{4}-\d{12}\.json$/;

// Login fields per source, in the order they are fetched, and the only domains its browser may reach.
export const SOURCES = {
  isracard: { company: CompanyTypes.isracard, fields: ['id', 'card6Digits', 'password'], label: 'Isracard', domains: ['isracard.co.il'] },
  hapoalim: { company: CompanyTypes.hapoalim, fields: ['userCode', 'password'], label: 'Bank Hapoalim', domains: ['bankhapoalim.co.il'] },
};

// The bank sites embed trackers and analytics (Google Tag Manager, Facebook, TikTok, Outbrain, ...) that run
// inside the logged-in page, and Chromium has its own Google services. Every other host name fails to
// resolve inside the browser, so neither can be reached; only the source's own domains and subdomains can.
// The container has no route to the internet anyway: FETCH_PROXY (the egress container) is its only way
// out and enforces the same allowlist (deploy/egress/allowed-domains) for every process, not only Chromium.
const proxy = process.env.FETCH_PROXY ? new URL(process.env.FETCH_PROXY) : null;

export function browserArgs(domains) {
  const allowed = domains.flatMap((d) => [`EXCLUDE ${d}`, `EXCLUDE *.${d}`]);
  if (proxy) allowed.push(`EXCLUDE ${proxy.hostname}`);
  return [
    `--host-resolver-rules=MAP * ~NOTFOUND, ${allowed.join(', ')}`,
    ...(proxy ? [`--proxy-server=${proxy.protocol}//${proxy.host}`] : []),
  ];
}

// stdin holds {"isracard": {...}, "hapoalim": {...}}, each optional. A bare Isracard login (the format
// before bank support) is still accepted.
export function validateCredentials(input) {
  const logins = input && typeof input === 'object' && 'password' in input ? { isracard: input } : input;
  if (!logins || typeof logins !== 'object') throw new Error('credentials must be a JSON object');
  const unknown = Object.keys(logins).filter((name) => !(name in SOURCES));
  if (unknown.length) throw new Error(`unknown credential source "${unknown[0]}"`);
  for (const [name, login] of Object.entries(logins)) {
    for (const key of SOURCES[name].fields) {
      if (typeof login?.[key] !== 'string' || !login[key]) {
        throw new Error(`${name} credentials are missing "${key}" (expected ${SOURCES[name].fields.join(', ')})`);
      }
    }
  }
  if (!Object.keys(logins).length) throw new Error('no credentials');
  return logins;
}

// Hapoalim asks for an SMS code on an unknown device, so it keeps its own browser profile (cookies) in
// stateDir between runs; Isracard needs no code and keeps nothing. hooks.requestCode is null in scheduled runs.
export async function fetchToInbox(source, credentials, { outDir, monthsBack, stateDir, hooks = {} }) {
  if (!Number.isInteger(monthsBack) || monthsBack < 0 || monthsBack > 11) throw new Error('FETCH_MONTHS_BACK must be 0-11');
  const { company, label, domains } = SOURCES[source];
  const now = new Date();
  const startDate = new Date(now.getFullYear(), now.getMonth() - monthsBack, 1);
  const options = {
    companyId: company,
    startDate,
    combineInstallments: false,
    executablePath: process.env.CHROMIUM_PATH ?? '/usr/bin/chromium',
    // The container drops all capabilities, so Chromium's own sandbox cannot start; the container is the boundary.
    args: ['--no-sandbox', '--disable-dev-shm-usage', ...browserArgs(domains), '--disable-component-update', '--disable-domain-reliability',
      '--disable-features=AutofillServerCommunication,InterestFeedContentSuggestions,MediaRouter,OptimizationHints'],
  };
  let scraper;
  if (source === 'hapoalim') {
    const userDataDir = join(stateDir, 'hapoalim-profile');
    mkdirSync(userDataDir, { recursive: true, mode: 0o700 });
    // A container that stopped mid-run leaves Chromium's profile lock behind; no other browser uses it.
    for (const lock of ['SingletonLock', 'SingletonCookie', 'SingletonSocket']) rmSync(join(userDataDir, lock), { force: true });
    const browser = await puppeteer.launch({ headless: true, executablePath: options.executablePath, args: options.args, userDataDir });
    scraper = new HapoalimWithCode({ ...options, browser }, hooks); // the library closes the browser
  } else {
    scraper = createScraper(options);
  }
  const result = await scraper.scrape(credentials);
  if (scraper.neededCode && !result.success) throw Object.assign(new Error(result.errorMessage), { needsCode: true });
  if (!result.success) {
    throw new Error(`${label} login or scrape failed: ${result.errorType ?? 'unknown'} ${result.errorMessage ?? ''}`.trim());
  }
  mkdirSync(outDir, { recursive: true });
  const stamp = now.toISOString().slice(0, 16).replace(/[-:T]/g, '');
  const accounts = [];
  for (const account of result.accounts ?? []) {
    const last4 = String(account.accountNumber).replace(/\D/g, '').slice(-4);
    const txns = account.txns.map(({ rawTransaction, ...txn }) => txn);
    const dates = txns.map((t) => t.date.slice(0, 10)).sort();
    accounts.push({ source, last4, transactions: txns.length, from: dates[0] ?? null, to: dates.at(-1) ?? null });
    if (!txns.length) {
      console.log(`${source} ${last4}: no transactions in the period, no file written`);
      continue;
    }
    const document = {
      format: 'israeli-bank-scrapers-account/v1',
      company: source,
      scraper_version: SCRAPER_VERSION,
      fetched_at: now.toISOString(),
      start_date: startDate.toISOString(),
      account_number: String(account.accountNumber),
      txns,
    };
    if (typeof account.balance === 'number') document.balance = account.balance;
    const file = join(outDir, `${source}-${last4}-${stamp}.json`);
    writeFileSync(`${file}.tmp`, JSON.stringify(document, null, 1), { mode: 0o640 });
    renameSync(`${file}.tmp`, file);
    console.log(`${source} ${last4}: ${txns.length} transactions ${dates[0]}..${dates.at(-1)} -> ${file}`);
  }
  return accounts;
}

export function pruneInbox(outDir, maxAgeDays) {
  const cutoff = Date.now() - maxAgeDays * 86400000;
  let removed = 0;
  for (const name of readdirSync(outDir)) {
    if (INBOX_FILE.test(name) && statSync(join(outDir, name)).mtimeMs < cutoff) {
      unlinkSync(join(outDir, name));
      removed += 1;
    }
  }
  return removed;
}
