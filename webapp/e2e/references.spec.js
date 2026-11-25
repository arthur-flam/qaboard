// Several references: the new commit is compared with one at a time, users switch between them in the header's rows
import { test, expect } from '@playwright/test';

const project = 'group/repo';
const commit = (id, message, branch = 'master') => ({
  id, hexsha: id, project_id: project, branch,
  committer_name: 'Alice', message,
  authored_datetime: '2026-09-01T10:00:00Z', committed_datetime: '2026-09-01T10:00:00Z',
  latest_output_datetime: '2026-09-01T10:00:00Z',
  batches: {}, data: {}, parents: [],
});
const a = 'a'.repeat(40), b = 'b'.repeat(40), c = 'c'.repeat(40), d = 'd'.repeat(40), e = 'e'.repeat(40), f = 'f'.repeat(40);
const commits = {
  [e]: commit(e, 'Faster tone mapping'),
  [f]: commit(f, 'Night mode', 'feature/night'),
  [a]: commit(a, 'Faster denoiser', 'feature/denoiser'),
  [b]: commit(b, 'Release 1.0'),
  [c]: { ...commit(c, 'Tuning the sharpening', 'tuning/sharpen'), batches: { default: { id: 1, label: 'default', valid_outputs: 12, failed_outputs: 2, running_outputs: 0, pending_outputs: 0, outputs: {}, data: {} } } },
  [d]: commit(d, 'Latest on master'),
};

const api = {
  '/api/v1/config': {},
  '/api/v1/user/me/': { is_authenticated: true, user_id: 1, user_name: 'alice', full_name: 'Alice', email: 'alice@example.com', login_type: 'local' },
  '/api/v1/projects': { [project]: { id: project, data: { qatools_config: {} }, latest_commit_datetime: '2026-09-01T10:00:00Z', total_commits: 1 } },
  '/api/v1/project': { qatools_config: {} },
  '/api/v1/milestones': { milestones: [{ id: 1, project, commit: b, batch: 'default', label: 'release 1.0', is_mine: true, shared: false }], favorite_groups: [] },
  '/api/v1/commits': Object.values(commits),
};

test.beforeEach(async ({ page }, testInfo) => {
  testInfo.errors_seen = [];
  page.on('console', msg => msg.type() === 'error' && testInfo.errors_seen.push(msg.text()));
  page.on('pageerror', error => testInfo.errors_seen.push(error.message));
  // the release notes' popup would hide the menus
  await page.addInitScript(() => localStorage.setItem('qaboard.release-notes.last-seen', '9999-12-31'));
  await page.route('**/api/**', route => {
    const { pathname } = new URL(route.request().url());
    const id = pathname.match(/^\/api\/v1\/commit\/([0-9a-f]+)$/)?.[1];
    if (id) return route.fulfill({ json: commits[id] });
    if (pathname.startsWith('/api/v1/commit')) return route.fulfill({ json: commits[d] });
    const key = Object.keys(api).sort((x, y) => y.length - x.length).find(k => pathname.startsWith(k));
    return route.fulfill({ json: key ? api[key] : {} });
  });
});

// oxlint-disable-next-line no-empty-pattern
test.afterEach(async ({}, testInfo) => {
  expect(testInfo.errors_seen, 'errors in the browser console').toEqual([]);
});

// The commit page's header: a row for the new results, one per reference
const header = page => page.getByRole('region', { name: 'What we compare' });
const comparing = (page, name) => expect(header(page).getByRole('radio', { name: `Comparing with ${name}` })).toBeVisible();
const row = (page, name) => header(page).locator('.qa-row').filter({ has: page.getByRole('button', { name: `${name}: rename`, exact: true }) });
// the names of the rows, in order
const row_names = page => header(page).locator('.qa-row .qa-side-text').allTextContents();

test('switch between references', async ({ page }) => {
  await page.goto(`/${project}/commit/${a}?reference=${b}&compare=${c}&compare=${d}`);
  // a milestone is called by its name, the others by their commit
  await expect.poll(() => row_names(page)).toEqual(['new', 'release 1.0', 'ccccccc', 'ddddddd']);
  await expect(header(page).getByRole('radio')).toHaveCount(3);
  await comparing(page, 'release 1.0');

  await header(page).getByRole('radio', { name: 'Compare with ccccccc' }).click();
  await comparing(page, 'ccccccc');
  await expect(page).toHaveURL(new RegExp(`reference=${c}`));
  // it stays in its row
  await expect.poll(() => row_names(page)).toEqual(['new', 'release 1.0', 'ccccccc', 'ddddddd']);
  await page.keyboard.press('r');
  await comparing(page, 'ddddddd');
  await page.waitForLoadState('networkidle');
});

test('add a reference, rename what we compare', async ({ page }) => {
  await page.goto(`/${project}/commit/${a}?reference=${b}`);
  await expect(page.getByText('Release 1.0').first()).toBeVisible();
  await header(page).getByRole('button', { name: 'Compare with another reference' }).click();
  await page.getByRole('textbox', { name: 'Add a commit or a branch as reference' }).fill(c);
  await page.keyboard.press('Enter');
  await expect(header(page).getByText('Tuning the sharpening').first()).toBeVisible();
  await expect(header(page).getByRole('radio')).toHaveCount(2);

  await page.getByRole('button', { name: 'new: rename' }).click();
  await page.getByRole('textbox', { name: 'Name instead of “new”' }).fill('ISP v2');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/label=ISP\+v2|label=ISP%20v2/);
  await expect(page.getByRole('button', { name: 'ISP v2: rename' })).toBeVisible();
  await page.waitForLoadState('networkidle');
});

// What users do with their references: add, duplicate, rename, reorder, remove. Each one keeps one name everywhere.
test('a reference has one name, in its row and the viewers', async ({ page }) => {
  await page.goto(`/${project}/commit/${a}?reference=${c}`);
  await expect(page.getByText('Tuning the sharpening').first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'ccccccc: rename' })).toBeVisible();

  await header(page).getByRole('button', { name: 'Compare with another reference' }).click();
  await page.getByRole('textbox', { name: 'Add a commit or a branch as reference' }).fill(d);
  await page.keyboard.press('Enter');
  // the one added is compared with
  await expect.poll(() => row_names(page)).toEqual(['new', 'ccccccc', 'ddddddd']);
  await comparing(page, 'ddddddd');

  // the "⋯" menu: duplicate the first one
  await header(page).getByRole('button', { name: 'Actions on ccccccc' }).click();
  await page.getByRole('menuitem', { name: /Compare with a copy/ }).click();
  // the copy is next to it, compared with
  await expect.poll(() => row_names(page)).toEqual(['new', 'ccccccc', 'ccccccc copy', 'ddddddd']);
  await comparing(page, 'ccccccc copy');

  // renamed, it keeps its name
  await page.getByRole('button', { name: 'ccccccc copy: rename' }).click();
  await page.getByRole('textbox', { name: /^Name instead of/ }).fill('sharper');
  await page.keyboard.press('Enter');
  await expect.poll(() => row_names(page)).toEqual(['new', 'ccccccc', 'sharper', 'ddddddd']);

  // dragged by its name before the first: it keeps its name
  await row(page, 'sharper').locator('.qa-row-label').dragTo(row(page, 'ccccccc'), { targetPosition: { x: 100, y: 4 } });
  await expect.poll(() => row_names(page)).toEqual(['new', 'sharper', 'ccccccc', 'ddddddd']);
  // one place down: after the half of the next row
  await row(page, 'sharper').locator('.qa-row-label').dragTo(row(page, 'ccccccc'), { targetPosition: { x: 100, y: 30 } });
  await expect.poll(() => row_names(page)).toEqual(['new', 'ccccccc', 'sharper', 'ddddddd']);
  // Alt+↓ on a row
  await row(page, 'sharper').locator('.qa-row-label [role="button"]').focus();
  await page.keyboard.press('Alt+ArrowDown');
  await expect.poll(() => row_names(page)).toEqual(['new', 'ccccccc', 'ddddddd', 'sharper']);

  // Delete removes it, with an undo
  await row(page, 'sharper').locator('.qa-row-label [role="button"]').focus();
  await page.keyboard.press('Delete');
  await expect.poll(() => row_names(page)).toEqual(['new', 'ccccccc', 'ddddddd']);
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect.poll(() => row_names(page)).toEqual(['new', 'ccccccc', 'ddddddd', 'sharper']);
  await page.waitForLoadState('networkidle');
});

test('the "Rename" menu item renames the row', async ({ page }) => {
  await page.goto(`/${project}/commit/${a}?reference=${c}&compare=${d}`);
  await header(page).getByRole('button', { name: 'Actions on ddddddd' }).click();
  await page.getByRole('menuitem', { name: /Rename/ }).click();
  const field = page.getByRole('textbox', { name: 'Name instead of “ddddddd”' });
  await expect(field).toBeFocused();
  await page.waitForTimeout(500);
  await expect(field).toBeFocused();
  await field.fill('night');
  await page.keyboard.press('Enter');
  await expect.poll(() => row_names(page)).toEqual(['new', 'ccccccc', 'night']);
  await page.waitForLoadState('networkidle');
});

test('rows say why references are there, and scroll when there are many', async ({ page }) => {
  // a milestone, two commits of master, a branch, copies with another batch and a filter
  const refs = [[d], [e], [f], [f, 'night'], [f, '', 'indoor'], [c]];
  const compare = refs.map(([id, batch, filter], i) => `compare[${i}][commit]=${id}${batch ? `&compare[${i}][batch]=${batch}` : ''}${filter ? `&compare[${i}][filter]=${filter}` : ''}`).join('&');
  await page.setViewportSize({ width: 1600, height: 700 });
  await page.goto(`/${project}/commit/${a}?reference=${b}&${compare}`);
  // copies are told apart by their batch, else their filter
  await expect.poll(() => row_names(page)).toEqual(['new', 'release 1.0', 'ddddddd', 'eeeeeee', 'fffffff', 'fffffff · night', 'fffffff · indoor', 'ccccccc']);
  await expect(header(page).getByText('7 references')).toBeVisible();
  await expect(header(page).getByText(/more below/)).toBeVisible();
  await page.locator('.qa-comparison-header').screenshot({ path: test.info().outputPath('wide.png') });
  // r reaches those out of sight
  for (const name of ['ddddddd', 'eeeeeee', 'fffffff', 'fffffff · night', 'fffffff · indoor', 'ccccccc']) {
    await page.keyboard.press('r');
    await comparing(page, name);
  }
  await expect(row(page, 'ccccccc')).toBeInViewport();
  await header(page).getByRole('button', { name: 'Show all' }).click();
  await expect(header(page).getByText(/more below/)).toHaveCount(0);

  // narrow: nothing overflows the window
  await page.setViewportSize({ width: 900, height: 600 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.locator('.qa-comparison-header').screenshot({ path: test.info().outputPath('narrow.png') });
  await page.waitForLoadState('networkidle');
});

test('drag the new results on a reference to swap them', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 600 });
  await page.goto(`/${project}/commit/${a}?label=ISP%20v2&reference=${c}&compare=${d}&compare=${e}`);
  await expect.poll(() => row_names(page)).toEqual(['ISP v2', 'ccccccc', 'ddddddd', 'eeeeeee']);
  await row(page, 'ISP v2').locator('.qa-row-label').dragTo(row(page, 'eeeeeee'));
  // names users gave follow their results
  await expect.poll(() => row_names(page)).toEqual(['new', 'ccccccc', 'ddddddd', 'ISP v2']);
  await comparing(page, 'ISP v2');
  await expect(header(page).getByText('Faster tone mapping').first()).toBeVisible();
  await page.waitForLoadState('networkidle');
});

// The reference's Change menu changes the reference compared, whichever it is in the list, and calls it by
// the milestone's name. A milestone that is a reference already is shown, not added twice.
test('a milestone picked in the Change menu replaces the reference compared, not another', async ({ page }) => {
  const pick = async name => {
    await page.getByRole('button', { name: 'Change the reference commit' }).click();
    // once on the page, it's listed first, "On this page", and again in its section
    await page.getByRole('menu', { name: 'Milestones' }).getByText(name, { exact: true }).first().click();
  };
  // c, then d compared, named by users
  await page.goto(`/${project}/commit/${a}?reference=${d}&label_ref=night&compare=${c}&ref_index=1`);
  await expect.poll(() => row_names(page)).toEqual(['new', 'ccccccc', 'night']);
  await comparing(page, 'night');
  await pick('release 1.0');
  await expect.poll(() => row_names(page)).toEqual(['new', 'ccccccc', 'release 1.0']);
  await comparing(page, 'release 1.0');
  await expect(page).toHaveURL(new RegExp(`reference=${b}`));
  await expect(page).toHaveURL(new RegExp(`compare=${c}`));

  // ...the first one compared: picking the milestone, a reference already, compares with it
  await header(page).getByRole('radio', { name: 'Compare with ccccccc' }).click();
  await comparing(page, 'ccccccc');
  await pick('release 1.0');
  await comparing(page, 'release 1.0');
  await expect.poll(() => row_names(page)).toEqual(['new', 'ccccccc', 'release 1.0']);
});
