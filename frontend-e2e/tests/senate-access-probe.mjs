// Manual, gated diagnosis. Use an ordinary system browser, not stealth,
// proxy rotation, identity overrides or a persisted/cross-machine session.
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright-core';

const HOME = 'https://efdsearch.senate.gov/search/home/';
const HOST = new URL(HOME).host;
const output = process.env.SENATE_ACCESS_OUTPUT;
if (process.env.GITHUB_ACTIONS !== 'true' ||
    process.env.SENATE_EFD_COLLECTION_ENABLED !== 'true' ||
    process.env.SENATE_EFD_TERMS_ACKNOWLEDGED !== 'true' || !output) {
  throw new Error('This probe requires the existing production authorization gates and a CI output path');
}
const executable = [
  '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
].find(existsSync);
if (!executable) throw new Error('No installed ordinary system browser was found');

const result = {
  schema_version: 'senate-browser-comparison/v1',
  os: process.platform,
  normal_browser: true,
  custom_identity: false,
  proxy: false,
  requests: [],
  recorded_at: new Date().toISOString(),
};
let browser;
try {
  browser = await chromium.launch({ executablePath: executable, headless: true });
  result.browser_version = browser.version();
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(15_000);
  page.on('response', response => {
    const url = new URL(response.url());
    if (url.host === HOST && response.request().isNavigationRequest()) {
      result.requests.push({ method: response.request().method(), path: url.pathname,
                             status: response.status() });
    }
  });
  const home = await page.goto(HOME, {waitUntil: 'load', timeout: 30_000});
  result.home_status = home?.status();
  result.home_title = (await page.title()).slice(0, 120);
  result.cookie_names = (await context.cookies(HOME)).map(cookie => cookie.name).sort();
  if (result.home_status !== 200) {
    result.outcome = 'home_refused';
  } else {
    const challenge = page.locator(
      'iframe[src*="recaptcha"],iframe[src*="hcaptcha"],iframe[src*="challenges.cloudflare.com"]');
    if (await challenge.count()) {
      result.outcome = 'human_verification_present';
    } else {
      const agreement = page.locator('input[name="prohibition_agreement"]');
      result.agreement_control_count = await agreement.count();
      result.form_controls = await page.locator('form input,form button').evaluateAll(
        controls => controls.map(control => ({tag: control.tagName.toLowerCase(),
          type: control.type, name: control.name,
          text: control.tagName === 'BUTTON' ? control.textContent.trim().slice(0, 60) : undefined})));
      if (result.agreement_control_count !== 1) {
        result.outcome = 'agreement_control_not_unique';
      } else {
        const form = agreement.locator('xpath=ancestor::form');
        result.form_action = await form.getAttribute('action');
        await agreement.check();
        const submit = form.locator('button[type="submit"],input[type="submit"]');
        result.submit_control_count = await submit.count();
        if (result.submit_control_count !== 1) {
          result.outcome = 'submit_control_not_unique';
        } else {
          // This is the site's ordinary form action under the existing gate.
          // Stop on any refusal or verification page; do not solve challenges.
          await submit.click();
          try { await page.waitForURL('https://efdsearch.senate.gov/search/', {timeout: 10_000}); }
          catch { /* Record the observed final page instead of retrying. */ }
          result.final_path = new URL(page.url()).pathname;
          result.final_title = (await page.title()).slice(0, 120);
          if (page.url() !== 'https://efdsearch.senate.gov/search/') {
            result.outcome = 'agreement_not_completed';
          } else {
            // One bounded catalog request through the actual browser session.
            // No report downloads, facts, tokens or cookie values leave it.
            result.catalog = await page.evaluate(async () => {
              const csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
              const cookie = document.cookie.split(';').map(value => value.trim())
                .find(value => value.startsWith('csrftoken='));
              const token = csrfInput?.value || (cookie ? decodeURIComponent(cookie.slice(10)) : null);
              if (!token) return {outcome: 'csrf_unavailable'};
              const body = new URLSearchParams({draw: '1', start: '0', length: '1',
                report_types: '[11]', filer_types: '[]', submitted_start_date: '10/01/2026 00:00:00',
                submitted_end_date: '', candidate_state: '', senator_state: '', office_id: '',
                first_name: '', last_name: '', csrfmiddlewaretoken: token});
              const response = await fetch('/search/report/data/', {method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded',
                  'X-CSRFToken': token, 'X-Requested-With': 'XMLHttpRequest'}, body});
              const type = response.headers.get('content-type') || '';
              if (!type.includes('json')) return {status: response.status, content_type: type};
              const value = await response.json();
              return {status: response.status, content_type: type,
                data_is_array: Array.isArray(value.data), returned_rows: value.data?.length,
                records_total: value.recordsTotal, records_filtered: value.recordsFiltered};
            });
            result.outcome = result.catalog.status === 200 && result.catalog.data_is_array
              ? 'catalog_access_confirmed' : 'catalog_not_confirmed';
          }
        }
      }
    }
  }
} catch (error) {
  result.outcome ||= 'probe_error';
  result.error_type = error.name;
  result.error_message = error.message.split('\n')[0].slice(0, 180);
} finally {
  await browser?.close();
  mkdirSync(path.dirname(output), {recursive: true});
  writeFileSync(output, JSON.stringify(result, null, 2));
  console.log('SENATE_BROWSER_ACCESS_RESULT=' + JSON.stringify(result));
}
