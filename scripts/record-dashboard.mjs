// Record an isolated live demo. Requires Playwright + Chromium and FFmpeg on the host.
import { createRequire } from 'node:module';
import { readFileSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { execFileSync } from 'node:child_process';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.PHOBOS_DEMO_URL || 'http://127.0.0.1:8094';
const tokenFile = process.env.PHOBOS_DEMO_TOKEN_FILE || '/tmp/phobos-dashboard-demo/owner-token';
const token = readFileSync(tokenFile, 'utf8').trim();
const output = resolve(process.env.PHOBOS_VIDEO_DIR || 'docs/assets');
mkdirSync(output, { recursive: true });
const temporary = mkdtempSync(join(tmpdir(), 'phobos-video-'));
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1,
  recordVideo: { dir: temporary, size: { width: 1440, height: 1000 } },
});
const errors = [];
const captureStarted = Date.now();
const page = await context.newPage();
page.on('pageerror', error => errors.push(error.message));
await page.goto(base + '/?demo=1');
await page.locator('#connect-button').click();
await page.locator('#owner-token').fill(token);
await page.getByRole('button', { name: 'Connect workspace' }).click();
await page.locator('#connection-dialog').waitFor({ state: 'hidden' });
await page.waitForTimeout(400);
await page.screenshot({ path: join(output, 'dashboard.png') });
const started = Date.now();
const at = async seconds => page.waitForTimeout(Math.max(0, started + seconds * 1000 - Date.now()));
const nav = view => page.locator(`.nav-item[data-view="${view}"]`).click();
await at(2);
await nav('conversation');
await page.locator('#workflow').selectOption('note');
await page.locator('#prompt').pressSequentially('Remember my launch checklist: review, simplify, then ship.', { delay: 24 });
await at(4.5);
await page.getByRole('button', { name: 'Send to Phobos' }).click();
await page.waitForFunction(() => document.querySelector('#chat-log').textContent.includes('Remember my launch checklist'));
await at(7);
await page.locator('#refresh-button').click();
await page.screenshot({ path: join(output, 'conversation.png') });
await nav('tickets');
await at(8.4);
await page.locator('#ticket-table [data-ticket]').first().click();
await page.locator('#detail-dialog').waitFor({ state: 'visible' });
if (!(await page.locator('#detail-content').textContent()).includes('Completed')) {
  throw new Error('Recorded local task did not complete');
}
await at(10.2);
await page.locator('#detail-dialog [data-close-dialog]').click();
await nav('memory');
await page.waitForFunction(() => document.querySelector('#graph-stage svg') !== null);
await page.screenshot({ path: join(output, 'memory.png') });
await at(14);
await nav('skills');
await page.locator('#skill-search').fill('REA');
await page.getByRole('button', { name: 'View skill' }).first().click();
await page.locator('#detail-dialog').waitFor({ state: 'visible' });
await at(17);
await page.locator('#detail-dialog [data-close-dialog]').click();
await nav('learning');
await at(19);
await nav('overview');
await at(20.7);
const video = page.video();
await context.close();
const raw = await video.path();
await browser.close();
if (errors.length) throw new Error('Dashboard browser errors: ' + errors.join('; '));
const result = join(output, 'phobos-dashboard-20s.mp4');
execFileSync('ffmpeg', [
  '-nostdin', '-v', 'error', '-y', '-ss', String((started - captureStarted) / 1000),
  '-i', raw, '-t', '20', '-vf', 'fps=30,setsar=1,tpad=stop_mode=clone:stop_duration=1', '-c:v', 'libx264',
  '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p', '-an', '-movflags', '+faststart', result,
], { stdio: 'inherit', timeout: 120000 });
const probe = JSON.parse(execFileSync('ffprobe', [
  '-v', 'error', '-show_entries', 'format=duration:stream=width,height,codec_name', '-of', 'json', result,
], { encoding: 'utf8' }));
if (Math.abs(Number(probe.format.duration) - 20) > 0.1) throw new Error('Video duration is not 20 seconds');
writeFileSync(join(output, 'walkthrough.json'), JSON.stringify({
  file: 'phobos-dashboard-20s.mp4', duration_seconds: Number(probe.format.duration),
  resolution: [1440, 1000], demo: true, live_provider: false,
  flow: ['overview', 'local note creation and completion', 'task history', 'Graphify memory', 'REA skill', 'reviewed learning'],
}, null, 2) + '\n');
console.log(JSON.stringify({ video: result, probe, browser_errors: errors }));
