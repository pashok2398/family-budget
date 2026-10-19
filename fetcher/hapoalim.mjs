// Bank Hapoalim login with the SMS code step that israeli-bank-scrapers lacks. The library fills the user
// code and password and clicks login; this replaces only what happens next. When the bank shows a code
// field, the code is requested from the person (typed in the app, passed by service.mjs), entered, and the
// "remember this device" option is ticked when offered, so the persistent browser profile may skip the SMS
// next time. Values typed into the page are never logged; only the page structure is, once, to diagnose
// layout changes.
import hapoalimModule from 'israeli-bank-scrapers/lib/scrapers/hapoalim.js';

const HapoalimScraper = hapoalimModule.default;
const HOME = /\/(portalserver\/HomePage|ng-portals(-bt)?\/rb\/he\/homepage)/i;
const LOGIN_FAILED = /errorcode=|\/MCP\/START|\/ABOUTTOEXPIRE\//i;

// Runs in the page: describe the visible form controls and mark the code input(s) and the remember-device
// checkbox with data attributes so they can be typed into and clicked from puppeteer.
export function inspectPage() {
  const visible = (el) => {
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const labelOf = (el) =>
    [el.getAttribute('aria-label'), el.placeholder, el.labels?.[0]?.innerText, el.closest('label')?.innerText,
      el.parentElement?.innerText]
      .filter(Boolean).join(' ').replace(/\s+/g, ' ').trim().slice(0, 80);
  const inputs = [...document.querySelectorAll('input')].filter(visible);
  const text = inputs.filter((el) => ['text', 'tel', 'number', 'password', ''].includes(el.type) &&
    !['userCode', 'password'].includes(el.id));
  // While the login form is still on screen, only an explicitly marked code field counts.
  const loginShown = ['userCode', 'password'].some((id) => document.getElementById(id) && visible(document.getElementById(id)));
  const strong = (el) => el.autocomplete === 'one-time-code' || /otp|sms|קוד|סיסמה חד/i.test(`${el.id} ${el.name} ${labelOf(el)}`);
  const hinted = text.filter((el) => strong(el) || (!loginShown && (/code/i.test(`${el.id} ${el.name}`) ||
    (el.maxLength >= 4 && el.maxLength <= 8))));
  // The bank's code screen (19.10.2026) has one box per digit, labelled "קוד אימות 1 מתוך 5" etc.
  const position = (el) => Number(labelOf(el).match(/(\d+)\s*מתוך\s*\d+/)?.[1] ?? 0);
  const boxes = text.filter((el) => /קוד/.test(labelOf(el)) && position(el)).sort((a, b) => position(a) - position(b));
  const single = text.filter((el) => el.maxLength === 1);
  const fields = boxes.length >= 4 ? boxes : hinted.length ? hinted.slice(0, 1) : single.length >= 4 && !loginShown ? single : [];
  document.querySelectorAll('[data-fb-code],[data-fb-remember]').forEach((el) => {
    el.removeAttribute('data-fb-code');
    el.removeAttribute('data-fb-remember');
  });
  fields.forEach((el, i) => el.setAttribute('data-fb-code', String(i)));
  const remember = inputs.find((el) => el.type === 'checkbox' && /זכור|זכירת|לזכור|מכשיר|remember|trust/i.test(labelOf(el)));
  if (remember) remember.setAttribute('data-fb-remember', remember.checked ? 'checked' : 'unchecked');
  const buttons = [...document.querySelectorAll('button, input[type=submit], [role=button]')].filter(visible);
  // "המשך" continues after the code; "כניסה" is the login form's own button, so it comes last.
  const buttonLabel = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
  const submit = [/^(המשך|אישור|אמת|אימות|continue|confirm)$/i, /המשך|אישור|אימות|continue|confirm/i, /^כניסה$/]
    .map((pattern) => buttons.find((el) => pattern.test(buttonLabel(el)) && !/שוב|חדש|קולי|resend|again/i.test(buttonLabel(el))))
    .find(Boolean);
  document.querySelectorAll('[data-fb-submit]').forEach((el) => el.removeAttribute('data-fb-submit'));
  if (submit) submit.setAttribute('data-fb-submit', '1');
  return {
    codeFields: fields.length,
    remember: remember ? (remember.checked ? 'checked' : 'unchecked') : 'none',
    submit: Boolean(submit),
    layout: {
      url: location.pathname,
      inputs: inputs.map((el) => ({ type: el.type, id: el.id, name: el.name, maxLength: el.maxLength, autocomplete: el.autocomplete, label: labelOf(el) })),
      buttons: buttons.map((el) => (el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 40)).filter(Boolean).slice(0, 15),
      headings: [...document.querySelectorAll('h1,h2,h3,[role=heading]')].filter(visible).map((el) => el.innerText.replace(/\s+/g, ' ').trim().slice(0, 80)).slice(0, 6),
    },
  };
}

async function waitFor(test, timeoutMs, description) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await test();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`timed out ${description}`);
}

export class HapoalimWithCode extends HapoalimScraper {
  // requestCode(): Promise<string> asks the person for the SMS code; null in scheduled runs, where a code
  // request ends the attempt at once instead of waiting for nobody.
  constructor(options, { requestCode, onCodeUsed }) {
    super(options);
    this.requestCode = requestCode;
    this.onCodeUsed = onCodeUsed;
  }

  getLoginOptions(credentials) {
    return { ...super.getLoginOptions(credentials), postAction: () => this.afterSubmit() };
  }

  async afterSubmit() {
    const page = this.page;
    const done = () => HOME.test(page.url()) || LOGIN_FAILED.test(page.url());
    const step = await waitFor(async () => {
      if (done()) return { done: true };
      const found = await page.evaluate(inspectPage).catch(() => null);
      return found?.codeFields ? found : null;
    }, 45000, 'waiting for the bank after login');
    if (step.done) return;

    console.log(`hapoalim: the bank asks for an SMS code; page layout ${JSON.stringify(step.layout)}`);
    this.neededCode = true;
    if (!this.requestCode) throw new Error('Bank Hapoalim asked for an SMS code; fetch from the app and type the code');
    const code = await this.requestCode();
    const fresh = await page.evaluate(inspectPage); // the page may have re-rendered while waiting
    if (!fresh.codeFields) throw new Error('the SMS code field disappeared (the bank page timed out); try again');
    if (fresh.codeFields > 1 && fresh.codeFields !== code.length) {
      throw new Error(`the bank expects a ${fresh.codeFields}-digit code; ${code.length} digits were entered`);
    }
    if (fresh.codeFields === 1) {
      await page.type('[data-fb-code="0"]', code, { delay: 60 });
    } else {
      for (const [i, digit] of [...code].entries()) await page.type(`[data-fb-code="${i}"]`, digit, { delay: 60 });
    }
    if (fresh.remember === 'unchecked') await page.click('[data-fb-remember]');
    console.log(`hapoalim: code entered; remember-device option: ${fresh.remember}; submit button: ${fresh.submit ? 'found' : 'none, pressing Enter'}`);
    if (fresh.submit) await page.click('[data-fb-submit]');
    else await page.keyboard.press('Enter');
    this.onCodeUsed?.();
    // A remember-this-device question may follow the code; accept it once.
    let remembered = fresh.remember !== 'none';
    try {
      await waitFor(async () => {
        if (done()) return true;
        if (!remembered) {
          const clicked = await page.evaluate(() => {
            const target = [...document.querySelectorAll('button, [role=button], input[type=checkbox]')].find((el) =>
              /זכור|לזכור|זכירת|מכשיר (קבוע|מוכר|זה)|remember|trust/i.test(el.innerText || el.getAttribute('aria-label') || el.closest('label')?.innerText || ''));
            if (!target || (target.type === 'checkbox' && target.checked)) return false;
            target.click();
            return true;
          }).catch(() => false);
          if (clicked) {
            remembered = true;
            console.log('hapoalim: accepted the remember-this-device option');
          }
        }
        return null;
      }, 60000, 'waiting for the bank to accept the SMS code (wrong or expired code?)');
    } catch (error) {
      const after = await page.evaluate(inspectPage).catch(() => null);
      console.log(`hapoalim: after the code the page stayed at ${JSON.stringify(after?.layout ?? null)}`);
      throw error;
    }
  }
}
