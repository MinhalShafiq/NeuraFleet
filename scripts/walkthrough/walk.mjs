// Drive the NeuraFleet dashboard in a real (headless) Chrome/Chromium, screenshot every screen,
// and assert what a person would check by eye.
//
//   cd scripts/walkthrough && npm install        # once (uses your installed Chrome; downloads no browser)
//   node walk.mjs all                            # the whole tour, including the stop/start-telemetry chaos step
//   node walk.mjs main                           # dashboard, LiDAR viewer, chat only (stack must be up)
//
// Env: BASE (default http://localhost:3000)   BROWSER (path to chrome/chromium/brave)   OUT (screenshot dir)
// Exit code is non-zero if any check fails or the browser logs an error.
import { execSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'
import { PNG } from 'pngjs'

const here = path.dirname(fileURLToPath(import.meta.url))
const phase = process.argv[2] || 'all'
const BASE = process.env.BASE || 'http://localhost:3000'
const OUT = process.env.OUT || path.resolve(here, '../../assets/screenshots')
fs.mkdirSync(OUT, { recursive: true })

const CANDIDATES = [
  '/usr/bin/google-chrome',
  '/usr/bin/google-chrome-stable',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
  '/snap/bin/chromium',
  '/opt/brave.com/brave/brave',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
]
const executablePath = process.env.BROWSER || CANDIDATES.find((p) => fs.existsSync(p))
if (!executablePath) {
  console.error('No Chrome/Chromium found. Install one, or set BROWSER=/path/to/chrome')
  process.exit(2)
}

// docker works directly if your shell has the docker group, else through `sg docker`
const compose = (args) => {
  const run = (cmd) => execSync(cmd, { cwd: path.resolve(here, '../..'), stdio: 'pipe' })
  try {
    run(`docker compose ${args}`)
  } catch {
    run(`sg docker -c "docker compose ${args}"`)
  }
}

const browser = await chromium.launch({
  executablePath,
  headless: true,
  // swiftshader = software WebGL, so the 3D viewer renders on machines without a GPU
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--no-sandbox'],
})
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

const problems = []
page.on('console', (m) => m.type() === 'error' && problems.push(`console.error: ${m.text().slice(0, 200)}`))
page.on('pageerror', (e) => problems.push(`pageerror: ${String(e).slice(0, 200)}`))
page.on('response', (r) => r.status() >= 400 && problems.push(`HTTP ${r.status()}: ${r.url()}`))

const shot = async (name) => {
  await page.screenshot({ path: `${OUT}/${name}.png` })
  console.log(`      screenshot: ${path.relative(process.cwd(), `${OUT}/${name}.png`)}`)
}
const check = (label, ok, extra = '') => {
  console.log(`${ok ? '  PASS' : '  FAIL'}  ${label}${extra ? `  (${extra})` : ''}`)
  if (!ok) problems.push(`check failed: ${label}`)
}
const body = async () => page.locator('body').innerText()
const nav = (label) => page.getByRole('button', { name: label }).first().click()
const section = (title) => console.log(`\n${title}`)
let t = '' // latest page text, shared by the phases

// Proof that points are drawn: the cloud changes every 200 ms while the grid and axes are static,
// so count pixels that differ between two canvas screenshots. (Counting "bright" pixels is fooled
// by the axis helper lines; this check fails on the old viewer that rendered an empty grid.)
const changedPixels = async () => {
  const a = PNG.sync.read(await page.locator('canvas').first().screenshot())
  await page.waitForTimeout(450)
  const b = PNG.sync.read(await page.locator('canvas').first().screenshot())
  let n = 0
  for (let i = 0; i < a.data.length; i += 4) {
    const d = Math.abs(a.data[i] - b.data[i]) + Math.abs(a.data[i + 1] - b.data[i + 1]) + Math.abs(a.data[i + 2] - b.data[i + 2])
    if (d > 60) n++
  }
  return n
}

await page.goto(BASE, { waitUntil: 'networkidle' })
await page.waitForTimeout(2500)

async function main() {
  section('1. Fleet dashboard')
  t = await body()
  check('six robots rendered', ['Atlas-1', 'Scout-2', 'Hauler-3', 'Sentinel-4', 'Mapper-5', 'Relay-6'].every((n) => t.includes(n)))
  check('no DEMO DATA badge while services are live', !/demo data/i.test(t))
  check('sidebar shows Connected', /Connected/.test(t))
  await shot('01-dashboard')

  section('2. Select a robot (its panel must keep updating)')
  await page.getByText('Atlas-1').nth(1).click().catch(() => page.getByText('Atlas-1').first().click())
  await page.waitForTimeout(800)
  const panel = async () =>
    (await page.locator('text=Live Telemetry').first().locator('xpath=ancestor::div[contains(@class,"glass-panel")]').innerText()).replace(/\s+/g, ' ')
  const p1 = await panel()
  await page.waitForTimeout(2500)
  check('live telemetry panel for the selected robot', /Live Telemetry/.test(p1))
  check('panel values change over time (not frozen at click time)', p1 !== (await panel()))
  await shot('02-robot-selected')

  section('3. LiDAR point-cloud viewer')
  await nav('LiDAR Viewer')
  await page.waitForSelector('canvas', { timeout: 10000 })
  await page.waitForFunction(() => document.body.innerText.includes('Streaming'), null, { timeout: 15000 }).catch(() => {})
  await page.waitForTimeout(3500)
  t = await body()
  check('stream connected ("Streaming")', /Streaming/.test(t))
  const pts = t.match(/Points:\s*([\d,]+)/)
  check('frames arriving', !!pts, pts ? `${pts[1]} points per frame` : '')
  const moved = await changedPixels()
  check('point cloud is actually drawn on the canvas', moved > 300, `${moved} pixels change between frames`)
  const f1 = (await body()).match(/Frame:\s*(\d+)/)?.[1]
  await shot('03-lidar-viewer')
  await page.waitForTimeout(2000)
  const f2 = (await body()).match(/Frame:\s*(\d+)/)?.[1]
  check('frame counter advances at about 5 Hz', f1 && f2 && Number(f2) - Number(f1) >= 5, `${f1} -> ${f2}`)
  const box = await page.locator('canvas').first().boundingBox()
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + 220, box.y + box.height / 2 - 60, { steps: 12 })
  await page.mouse.up()
  await page.mouse.wheel(0, -400)
  await page.waitForTimeout(1200)
  await shot('04-lidar-orbited')

  section('4. Fleet chat (RAG)')
  await nav('Fleet Chat')
  await page.waitForTimeout(800)
  await page.getByText('What are the safety protocols?').first().click()
  await page.locator('textarea, input[type="text"]').first().press('Enter').catch(() => {})
  await page.waitForFunction(() => /safety/i.test(document.body.innerText) && document.body.innerText.includes('.txt'), null, { timeout: 20000 }).catch(() => {})
  await page.waitForTimeout(800)
  t = await body()
  check('answer grounded in the documentation', /safety/i.test(t) && /Relevant information|Emergency stop|E-Stop/i.test(t))
  check('source documents shown as chips', /safety_protocols\.txt|troubleshooting\.txt|robot_manual\.txt/.test(t))
  check('no raw heading underlines in the answer', !/-{5,}|={5,}/.test(t))
  await shot('05-chat')

  section('5. Back to the dashboard')
  await nav('Dashboard')
  await page.waitForTimeout(4500)
  await shot('06-dashboard-charts')
}

async function chaos() {
  section('6. Chaos: stop the telemetry service while the dashboard is open')
  compose('stop telemetry-service')
  await page.waitForFunction(() => /demo data/i.test(document.body.innerText), null, { timeout: 30000 }).catch(() => {})
  t = await body()
  check('DEMO DATA badge appears', /demo data/i.test(t))
  check('robots keep rendering (gateway fallback)', t.includes('Atlas-1'))
  await shot('07-demo-data')

  section('7. Restart it: the badge must clear by itself')
  compose('start telemetry-service')
  await page.waitForFunction(() => !/demo data/i.test(document.body.innerText), null, { timeout: 90000 }).catch(() => {})
  t = await body()
  check('badge clears after recovery, no reload needed', !/demo data/i.test(t))
  await shot('08-recovered')
}

try {
  if (phase === 'main' || phase === 'all') await main()
  if (phase === 'all' || phase === 'chaos') await chaos()
} finally {
  // Whatever happened, leave the stack the way we found it
  if (phase === 'all' || phase === 'chaos') {
    try {
      compose('start telemetry-service')
    } catch {}
  }
}

console.log(`\nbrowser problems: ${problems.length ? '' : 'none'}`)
problems.forEach((p) => console.log(`   ${p}`))
await browser.close()
process.exit(problems.length ? 1 : 0)
