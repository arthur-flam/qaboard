// The Runs page: a project's runs across commits, by experiment, their curves on the same plots, driven by the
// table, and a run opened in place. Against a mocked API (fixtures/runs.js).
import { test, expect } from '@playwright/test';
import { mockRunsApi, project } from './fixtures/runs.js';

const errors = [];
test.beforeEach(async ({ page }) => {
  errors.length = 0;
  page.on('console', msg => msg.type() === 'error' && errors.push(msg.text()));
  page.on('pageerror', error => errors.push(error.message));
});
test.afterEach(() => {
  expect(errors, 'errors in the browser console').toEqual([]);
});

const plot = (page, title) => page.getByTestId('runs-plots').locator('.js-plotly-plot').filter({ has: page.locator('.gtitle', { hasText: title }) });

test('experiments as a tree, their runs across commits, the table driving the plots', async ({ page }) => {
  await mockRunsApi(page);
  await page.goto(`/${project}/runs`);
  // the sidebar has the page, the tree every experiment
  await expect(page.getByRole('navigation', { name: 'Navigation' }).getByText('Runs', { exact: true })).toBeVisible();
  const tree = page.getByRole('navigation', { name: 'Experiments' });
  await expect(tree.getByRole('link', { name: 'detector' })).toBeVisible();
  await expect(page.getByRole('table', { name: 'Runs' }).locator('tbody tr.qa-run')).toHaveCount(17);

  // a level: its runs and those under it
  await tree.getByRole('button', { name: 'Unfold detector' }).click();
  await tree.getByRole('link', { name: 'lr-sweep' }).click();
  await expect(page).toHaveURL(new RegExp(`/${project}/runs/detector/lr-sweep$`));
  const rows = page.getByRole('table', { name: 'Runs' }).locator('tbody tr.qa-run');
  await expect(rows).toHaveCount(11);
  const loss = plot(page, 'val/loss');
  await expect(loss.locator('.scatterlayer .trace')).toHaveCount(11);

  // the eye hides a run from the plots
  await page.getByRole('button', { name: 'Hide lr=0.0001 · seed=0 · batch_size=64 in the plots' }).or(page.getByRole('button', { name: /^Hide lr=0.0001/ })).first().click();
  await expect(loss.locator('.scatterlayer .trace')).toHaveCount(10);
  await expect(page).toHaveURL(/hidden=/);

  // selecting rows plots only them
  // the address changes, then the page: check() would look too soon. Blueprint's indicator covers the input.
  for (const name of [/^Select lr=0.01 /, /^Select lr=0.003 /]) {
    await page.getByRole('checkbox', { name }).click({ force: true });
    await expect(page.getByRole('checkbox', { name })).toBeChecked();
  }
  await expect(loss.locator('.scatterlayer .trace')).toHaveCount(2);
  await expect(page.getByTestId('runs-plots').getByText('2 runs selected, 5 series')).toBeVisible();

  // two selected runs compare: the first with the second as its reference, in a panel
  await page.getByRole('button', { name: 'Compare the 2 selected' }).click();
  const panel = page.locator('.bp6-drawer');
  await expect(panel.getByText(/^Compared with/)).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Open on the commit page' })).toHaveAttribute('href', /reference=/);
  await expect(page).toHaveURL(/vs=/);
  // Esc closes it even with the focus behind, and Back doesn't open it again
  await page.getByRole('heading', { name: 'Runs', exact: true }).click({ force: true });
  await page.keyboard.press('Escape');
  await expect(panel).toHaveCount(0);
  await expect(page).not.toHaveURL(/run=/);

  // the quick filters
  await page.getByRole('button', { name: 'Only failed' }).click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText('lr=0.01');
});

test('runs grouped by a parameter: the mean and range of each group', async ({ page }) => {
  await mockRunsApi(page);
  await page.goto(`/${project}/runs/detector/lr-sweep?group_by=lr&lines=mean`);
  const loss = plot(page, 'val/loss');
  // five learning rates; the two with several seeds have a band (two traces each)
  await expect(loss.locator('.scatterlayer .trace')).toHaveCount(5 + 2 * 2);
  await expect(page.locator('tr.qa-group')).toHaveCount(5);
  await page.getByRole('radio', { name: 'Each run' }).click();
  await expect(loss.locator('.scatterlayer .trace')).toHaveCount(11);
});

test('a project that trains opens on its runs', async ({ page }) => {
  await mockRunsApi(page, { home: 'runs' });
  await page.goto(`/${project}`);
  await expect(page).toHaveURL(new RegExp(`/${project}/runs$`));
  // Runs comes first in the sidebar
  const items = page.getByRole('navigation', { name: 'Navigation' }).locator('.bp6-menu-item');
  await expect(items.first()).toContainText('Runs');
});
