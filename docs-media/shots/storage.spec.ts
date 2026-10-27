// Storage usage (docs-media/shots.yaml: storage/*). Nothing is deleted: the delete dialog is cancelled.
//
// - The usage comes from the `storage_usage` table, which the backend's jobs worker computes when it first starts on a
//   database (a restored snapshot): storageUsageReady() waits for it.
// - Users see other people's usage in the projects where they're members (a grant, a group...): Ines, the camera ISP's
//   lead, is made a developer of kitefin/camera-isp while these shots are taken. Adaeze is the demo stack's admin.
// - "Updated <when>" says when the server last computed the usage, by the server's clock (the time of the capture), while
//   the page's clock is at T0: it's masked.
import { test } from '@playwright/test';
import { shot, type Page } from './lib';
import { demo } from './lib/demo';
import { ADMIN, apiAs, removeMembers, setMembers, storageUsageReady } from './demoState';

const ISP = 'kitefin/camera-isp';
const MEMBERS = { ines: 'developer' } as const;

test.beforeAll(async () => {
  const admin = await apiAs(demo(), ADMIN);
  try {
    await storageUsageReady(admin);
    await setMembers(admin, ISP, MEMBERS);
  } finally {
    await admin.dispose();
  }
});

test.afterAll(async () => {
  const admin = await apiAs(demo(), ADMIN);
  try {
    await removeMembers(admin, ISP, Object.keys(MEMBERS));
  } finally {
    await admin.dispose();
  }
});

// Narrower than the default, so that the tables' text stays readable once the shot is scaled down in a page or an email
const VIEWPORT = { width: 1200, height: 1100 };
const updated = (page: Page) => [page.getByText(/^Updated /)];
const rows = (page: Page) => page.getByRole('table').last().locator('tbody > tr');

shot('storage/batches', {
  project: null,
  login: 'ines',
  url: d => `${d.baseUrl}/storage/batches?scope=all&project=${encodeURIComponent(ISP)}`,
  alt: 'Storage usage of everyone\'s runs in kitefin/camera-isp: 2.2 GB in 42 batches, mostly the CI\'s. The Batches '
    + 'tab lists them biggest first, each with its commit, branch, folder and who ran it; the two milestones have a lock.',
  caption: 'The biggest batches first: milestones are locked, the others can be checked and their files deleted.',
  viewport: VIEWPORT,
  mask: updated,
  actions: async ui => {
    await rows(ui.page).nth(6).waitFor();
    await ui.cursor.moveTo({ x: 1180, y: 1080 });
    await ui.settle();
  },
  target: page => [page.getByRole('heading', { name: 'Storage usage' }), rows(page).nth(5)],
});

shot('storage/delete', {
  project: null,
  login: 'ines',
  url: d => `${d.baseUrl}/storage/batches?scope=all&project=${encodeURIComponent(ISP)}`,
  alt: 'The "Delete the runs\' files" dialog for every run of kitefin/camera-isp: it frees the files of Ines\'s runs, '
    + 'keeps the milestones and the runs other people ran, and asks to type a sentence to confirm.',
  caption: 'Before deleting, QA-Board says how much space it frees, and what it keeps.',
  viewport: VIEWPORT,
  mask: updated,
  actions: async ui => {
    await ui.click(ui.page.getByRole('button', { name: /^Delete all runs that match/ }));
    const dialog = ui.page.getByRole('dialog', { name: 'Delete the runs\' files' });
    await dialog.getByText('Kept', { exact: true }).waitFor();
    await ui.cursor.moveTo({ x: 1180, y: 1080 });
    await ui.settle();
  },
  target: page => page.getByRole('dialog', { name: 'Delete the runs\' files' }),
  padding: 24,
});

shot('storage/users', {
  project: null,
  login: ADMIN,
  url: d => `${d.baseUrl}/storage/users?scope=all`,
  alt: 'Storage usage, the Users tab of everyone\'s runs: five people use 3.7 GB. The CI uses 91% of it, mostly in '
    + 'kitefin/camera-isp, then Ines, Arjun, Mira and Kaito, each with their share, runs, batches and biggest project.',
  caption: 'Who uses the most, and where: click someone to see their runs.',
  viewport: VIEWPORT,
  mask: updated,
  actions: async ui => {
    await rows(ui.page).nth(4).waitFor();
    await ui.cursor.moveTo({ x: 1180, y: 1080 });
    await ui.settle();
  },
  target: page => [page.getByRole('heading', { name: 'Storage usage' }), rows(page).last()],
});

shot('storage/folders', {
  project: null,
  login: 'ines',
  url: d => `${d.baseUrl}/storage/folders`,
  alt: 'Storage usage, the Folders tab of Ines\'s runs: it starts in the output folder of her commit, where her runs '
    + 'split in two subfolders, 181 MB and 14.6 MB, with a box to paste a folder, her biggest folder, and a button to '
    + 'delete the files of these runs.',
  caption: 'Folders start where your runs split: paste the folder of a quota alert to see what QA-Board knows there.',
  viewport: VIEWPORT,
  mask: updated,
  actions: async ui => {
    await rows(ui.page).nth(1).waitFor();
    await ui.cursor.moveTo({ x: 1180, y: 1080 });
    await ui.settle();
  },
  target: page => [page.getByRole('tablist'), rows(page).last()],
});
