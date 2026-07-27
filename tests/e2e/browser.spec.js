/**
 * browser.spec.js
 *
 * End-to-end suite for the CollectiveFS console: the Files explorer and the
 * System & Infrastructure section, including the section chat's ability to
 * actually change node configuration.
 *
 * Run with:
 *   npx playwright test tests/e2e/browser.spec.js
 *
 * The webServer in playwright.config.js starts the API on :8000, which also
 * serves the built UI from ui/dist. Build the UI first (`cd ui && npm run build`).
 */

import { expect, test } from '@playwright/test'

// ── helpers ─────────────────────────────────────────────────────────

// Tests run against a real node that may already hold data (both browser
// projects share one store), so fixtures get unique names rather than
// assuming an empty file list.
let seq = 0
const RUN = Math.random().toString(36).slice(2, 7)
function uniq(name) {
  seq += 1
  const dot = name.lastIndexOf('.')
  const stem = dot === -1 ? name : name.slice(0, dot)
  const ext = dot === -1 ? '' : name.slice(dot)
  return `${stem}-${RUN}${seq}${ext}`
}

/** Upload through the API from inside the page so it hits the same origin. */
async function uploadFile(page, name, content = 'CollectiveFS test data', folder = '') {
  return page.evaluate(
    async ({ name, content, folder }) => {
      const form = new FormData()
      form.append('file', new Blob([content], { type: 'application/octet-stream' }), name)
      form.append('folder', folder)
      const response = await fetch('/api/files/upload', { method: 'POST', body: form })
      if (!response.ok) throw new Error(`Upload failed: ${response.status} ${await response.text()}`)
      return (await response.json()).id
    },
    { name, content, folder },
  )
}

async function apiJson(page, path, init) {
  return page.evaluate(
    async ({ path, init }) => {
      const response = await fetch(path, init)
      const text = await response.text()
      return { status: response.status, body: text ? JSON.parse(text) : null }
    },
    { path, init },
  )
}

async function readConfig(page) {
  const { body } = await apiJson(page, '/api/config')
  return body.config
}

async function writeConfig(page, updates) {
  return apiJson(page, '/api/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ updates }),
  })
}

/** Chat pinned to the deterministic provider so the suite never needs an LLM. */
async function chat(page, section, message) {
  const { body } = await apiJson(page, '/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ section, message, provider: 'builtin' }),
  })
  return body
}

async function openSection(page, id) {
  await page.goto('/')
  await page.waitForSelector(`[data-testid="section-${id}"]`)
  await page.locator(`[data-testid="section-${id}"] .section-title-button`).click()
  await expect(page).toHaveURL(new RegExp(`/sections/${id}`))
  await page.waitForSelector('.section-card.full')
}

// ── shell ───────────────────────────────────────────────────────────

test.describe('console shell', () => {
  test('renders both sections with Files first', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.topbar-brand')).toHaveText('CollectiveFS')

    // toHaveText on the locator, not allTextContents(): the latter is a
    // one-shot read with no auto-waiting, and the section cards only render
    // after the first telemetry fetch resolves. On a cold server that lands
    // after the brand does, so the snapshot caught an empty list.
    await expect(page.locator('.section-card-title'))
      .toHaveText(['Files', 'System & Infrastructure'])
  })

  test('each section offers dashboard, chat and skill views', async ({ page }) => {
    await page.goto('/')
    const card = page.locator('[data-testid="section-files"]')
    // dashboard, chat, skill, collapse
    await expect(card.locator('.section-toggle button')).toHaveCount(4)

    await card.locator('button[aria-label^="Chat"]').click()
    await expect(card.locator('[data-testid="section-chat-files"]')).toBeVisible()

    await card.locator('button[aria-label^="Show Files skill"]').click()
    await expect(card.locator('.section-skill-doc')).toContainText('Reed-Solomon')
  })

  test('a section collapses and stays collapsed across reloads', async ({ page }) => {
    await page.goto('/')
    const card = page.locator('[data-testid="section-system"]')
    await card.locator('button[aria-label="Collapse section"]').click()
    await expect(card).toHaveClass(/collapsed/)

    await page.reload()
    await expect(page.locator('[data-testid="section-system"]')).toHaveClass(/collapsed/)

    await page.locator('[data-testid="section-system"] button[aria-label="Expand section"]').click()
    await expect(page.locator('[data-testid="section-system"]')).not.toHaveClass(/collapsed/)
  })
})

// ── files explorer ──────────────────────────────────────────────────

test.describe('files explorer', () => {
  test('navigates the folder tree by row, tree and breadcrumb', async ({ page }) => {
    await page.goto('/')
    const nested = uniq('nested.txt')
    await uploadFile(page, nested, 'nested', 'e2e/inner')
    await openSection(page, 'files')
    await expect(page.locator('[data-testid="file-explorer"]')).toBeVisible()

    await page.locator('.entry-row .entry-name', { hasText: 'e2e' }).first().click()
    await expect(page).toHaveURL(/path=e2e/)
    await expect(page.locator('.crumb')).toHaveText(['All Files', 'e2e'])

    // The tree auto-expands to follow navigation.
    await page.locator('.tree-row .tree-name', { hasText: 'inner' }).first().click()
    await expect(page).toHaveURL(/path=e2e%2Finner/)
    await expect(page.locator('.entry-row .entry-name', { hasText: nested })).toHaveCount(1)

    await page.locator('.crumb', { hasText: 'All Files' }).click()
    await expect(page).not.toHaveURL(/path=/)
  })

  test('switches between list and grid views', async ({ page }) => {
    await page.goto('/')
    await uploadFile(page, uniq('view-mode.txt'))
    await openSection(page, 'files')

    await expect(page.locator('.entry-list')).toBeVisible()
    await page.locator('button[aria-label="Grid view"]').click()
    await expect(page.locator('.entry-grid')).toBeVisible()
    await expect(page.locator('.entry-tile').first()).toBeVisible()

    await page.locator('button[aria-label="List view"]').click()
    await expect(page.locator('.entry-list')).toBeVisible()
  })

  test('search reaches files in nested folders', async ({ page }) => {
    await page.goto('/')
    const needle = uniq('buried-needle.txt')
    await uploadFile(page, needle, 'needle', 'deep/deeper')
    await openSection(page, 'files')

    // Not a direct child of the root, so only search should surface it.
    await expect(page.locator('.entry-row .entry-name', { hasText: needle })).toHaveCount(0)
    await page.locator('.explorer-search').fill(needle)
    await expect(page.locator('.entry-row .entry-name', { hasText: needle })).toHaveCount(1)
  })

  test('shows shard health and a shard map for a stored file', async ({ page }) => {
    await page.goto('/')
    const mapped = uniq('shard-map.bin')
    await uploadFile(page, mapped, 'x'.repeat(4096))
    await openSection(page, 'files')

    const row = page.locator('.entry-row', { has: page.locator('.entry-name', { hasText: mapped }) })
    await expect(row).toBeVisible({ timeout: 15000 })
    await expect(row.locator('.shard-bar')).toBeVisible()

    await row.locator('.entry-name').click()
    const detail = page.locator('[data-testid="file-detail"]')
    await expect(detail).toBeVisible()
    await expect(detail).toContainText('Shard map')
    expect(await detail.locator('.shard-cell').count()).toBeGreaterThan(0)
  })

  test('renames and moves a file from the detail drawer', async ({ page }) => {
    await page.goto('/')
    const before = uniq('before-rename.txt')
    const after = uniq('after-rename.txt')
    const id = await uploadFile(page, before, 'rename me')
    await apiJson(page, '/api/folders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: 'renamed-into' }),
    })
    await openSection(page, 'files')

    await page.locator('.entry-row .entry-name', { hasText: before }).click()
    const detail = page.locator('[data-testid="file-detail"]')
    await detail.locator('input[aria-label="File name"]').fill(after)
    await detail.locator('select[aria-label="Folder"]').selectOption('renamed-into')
    await detail.locator('button', { hasText: 'Save' }).click()

    await expect.poll(async () => (await apiJson(page, `/api/files/${id}`)).body).toMatchObject({
      name: after,
      folder: 'renamed-into',
    })
  })

  test('creates a folder and removes it without deleting files', async ({ page }) => {
    await page.goto('/')
    const folder = uniq('doomed').replace('.', '-')
    const id = await uploadFile(page, uniq('survives-folder-delete.txt'), 'keep me', folder)
    await openSection(page, 'files')

    const row = page.locator('.entry-row', { has: page.locator('.entry-name', { hasText: folder }) })
    page.once('dialog', (dialog) => dialog.accept())
    await row.locator(`button[aria-label="Remove folder ${folder}"]`).click()

    // The folder is gone but the file survived, relocated to the root.
    await expect.poll(async () => (await apiJson(page, `/api/files/${id}`)).body.folder).toBeNull()
    await expect.poll(async () => (await apiJson(page, `/api/files/${id}`)).status).toBe(200)
  })

  test('deletes a file from the detail drawer', async ({ page }) => {
    await page.goto('/')
    const name = uniq('delete-me.txt')
    const id = await uploadFile(page, name, 'temporary')
    await openSection(page, 'files')

    await page.locator('.entry-row .entry-name', { hasText: name }).click()
    await page.locator('[data-testid="file-detail"] button', { hasText: 'Delete' }).click()
    await expect.poll(async () => (await apiJson(page, `/api/files/${id}`)).status).toBe(404)
  })
})

// ── system section ──────────────────────────────────────────────────

test.describe('system & infrastructure', () => {
  test('renders every telemetry panel', async ({ page }) => {
    await openSection(page, 'system')
    // Settle the panel list before snapshotting it — same one-shot-read hazard
    // as the section titles above.
    await expect(page.locator('.skill-panel-block h3').first()).toBeVisible()
    const panels = await page.locator('.skill-panel-block h3').allTextContents()
    for (const expected of ['Storage & Quota', 'Compute', 'Memory', 'Network', 'Durability', 'Peers & Contracts']) {
      expect(panels).toContain(expected)
    }
    await expect(page.locator('.mini-stat', { hasText: 'NODE' })).toBeVisible()
  })

  test('charts fill in from live polling', async ({ page }) => {
    await openSection(page, 'system')
    // The poller samples every 5s and a chart needs two points to draw.
    await expect(page.locator('.recharts-wrapper').first()).toBeVisible({ timeout: 30000 })
    await expect.poll(async () => page.locator('.recharts-wrapper').count(), { timeout: 30000 }).toBeGreaterThanOrEqual(4)
  })

  test('quota and erasure state reflect the stored configuration', async ({ page }) => {
    await page.goto('/')
    await writeConfig(page, { 'erasure.data_shards': 10, 'erasure.parity_shards': 4 })
    await openSection(page, 'system')
    await expect(page.locator('.status-pill', { hasText: '10+4 erasure' })).toBeVisible()
  })
})

// ── configuration ───────────────────────────────────────────────────

test.describe('configuration', () => {
  test('a quick control writes the change through', async ({ page }) => {
    await openSection(page, 'system')
    const field = page.locator('#storage\\.quota_bytes')
    await field.fill('123GB')
    await page.locator('.setting-cell', { has: field }).locator('button', { hasText: 'Set' }).click()

    await expect.poll(async () => (await readConfig(page)).storage.quota_bytes).toBe(123 * 1024 ** 3)
    await expect(page.locator('.toast')).toBeVisible()
  })

  test('an impossible quota is rejected with a reason', async ({ page }) => {
    await page.goto('/')
    const before = (await readConfig(page)).storage.quota_bytes
    const result = await writeConfig(page, { 'storage.quota_bytes': '999PB' })

    expect(result.status).toBe(400)
    expect(result.body.detail).toContain('exceeds the filesystem size')
    expect((await readConfig(page)).storage.quota_bytes).toBe(before)
  })

  test('an out-of-range shard count is rejected', async ({ page }) => {
    await page.goto('/')
    const result = await writeConfig(page, { 'erasure.parity_shards': 99 })
    expect(result.status).toBe(400)
    expect(result.body.detail).toContain('at most 32')
  })

  test('every change lands in the audit log', async ({ page }) => {
    await page.goto('/')
    await writeConfig(page, { 'contracts.max_peers': 41 })
    const { body } = await apiJson(page, '/api/config/audit?limit=5')
    const fields = body.entries.flatMap((entry) => entry.changes.map((change) => change.field))
    expect(fields).toContain('contracts.max_peers')
  })
})

// ── section chat ────────────────────────────────────────────────────

test.describe('section chat', () => {
  test('offers all four providers and marks the active one', async ({ page }) => {
    await page.goto('/')
    const card = page.locator('[data-testid="section-system"]')
    await card.locator('button[aria-label^="Chat"]').click()

    const buttons = card.locator('.provider-switch button')
    await expect(buttons).toHaveCount(4)
    await expect(buttons).toHaveText(['Codewhale', 'Claude Code', 'Codex', 'Built-in'])
    await expect(card.locator('.provider-switch button.active')).toHaveCount(1)
  })

  test('switching provider persists to the node config', async ({ page }) => {
    await page.goto('/')
    const card = page.locator('[data-testid="section-system"]')
    await card.locator('button[aria-label^="Chat"]').click()
    await card.locator('.provider-switch button', { hasText: 'Built-in' }).click()

    await expect.poll(async () => (await readConfig(page)).agent.provider).toBe('builtin')
    await page.reload()
    await page.locator('[data-testid="section-system"] button[aria-label^="Chat"]').click()
    await expect(
      page.locator('[data-testid="section-system"] .provider-switch button.active'),
    ).toHaveText('Built-in')
  })

  test('changes allocated storage from a plain-language instruction', async ({ page }) => {
    await page.goto('/')
    await writeConfig(page, { 'storage.quota_bytes': '40GB' })

    const reply = await chat(page, 'system', 'allocate 90GB to the collective')
    expect(reply.error).toBeNull()
    expect(reply.applied).toHaveLength(1)
    expect(reply.applied[0]).toMatchObject({
      field: 'storage.quota_bytes',
      before: 40 * 1024 ** 3,
      after: 90 * 1024 ** 3,
    })
    expect((await readConfig(page)).storage.quota_bytes).toBe(90 * 1024 ** 3)
  })

  test('adjusts erasure parameters and reports the new fault budget', async ({ page }) => {
    await page.goto('/')
    await writeConfig(page, { 'erasure.parity_shards': 4 })

    const reply = await chat(page, 'system', 'set parity shards to 7')
    expect(reply.applied[0]).toMatchObject({ field: 'erasure.parity_shards', after: 7 })
    expect(reply.reply).toContain('Existing files keep the layout')
    expect((await readConfig(page)).erasure.parity_shards).toBe(7)
  })

  test('applies a relative change against the current value', async ({ page }) => {
    await page.goto('/')
    await writeConfig(page, { 'storage.quota_bytes': '50GB' })

    const reply = await chat(page, 'system', 'increase space by 10GB')
    expect(reply.applied[0].after).toBe(60 * 1024 ** 3)
  })

  test('toggles a boolean setting', async ({ page }) => {
    await page.goto('/')
    await writeConfig(page, { 'contracts.challenges_enabled': true })

    const reply = await chat(page, 'system', 'disable challenges')
    expect(reply.applied[0]).toMatchObject({ field: 'contracts.challenges_enabled', after: false })
  })

  test('refuses an invalid change and leaves config untouched', async ({ page }) => {
    await page.goto('/')
    const before = (await readConfig(page)).storage.quota_bytes

    const reply = await chat(page, 'system', 'allocate 900TB to the collective')
    expect(reply.applied).toHaveLength(0)
    expect(reply.error).toContain('exceeds the filesystem size')
    expect(reply.reply).toContain('Not applied')
    expect((await readConfig(page)).storage.quota_bytes).toBe(before)
  })

  test('answers a question without changing anything', async ({ page }) => {
    await page.goto('/')
    const before = await readConfig(page)

    const reply = await chat(page, 'system', 'how much headroom is left?')
    expect(reply.applied).toHaveLength(0)
    expect(reply.reply).toContain('Erasure coding')
    expect(await readConfig(page)).toEqual(before)
  })

  test('the UI renders an applied change inline in the chat log', async ({ page }) => {
    await page.goto('/')
    await writeConfig(page, { 'agent.provider': 'builtin', 'contracts.max_peers': 12 })

    const card = page.locator('[data-testid="section-system"]')
    await card.locator('button[aria-label^="Chat"]').click()
    await card.locator('input[aria-label^="Ask"]').fill('set max peers to 24')
    await card.locator('button[aria-label="Send message"]').click()

    await expect(card.locator('.chat-applied-title')).toHaveText('Configuration applied', { timeout: 30000 })
    await expect(card.locator('.chat-applied-row code')).toHaveText('contracts.max_peers')
    await expect(card.locator('.chat-applied-row .after')).toHaveText('24')
    expect((await readConfig(page)).contracts.max_peers).toBe(24)
  })
})
