// A project that trains, for the Runs page's tests and screenshots: a learning rate sweep with seeds, one run that
// diverges and one still running, a warmup study under it, a backbone comparison, a quantization study.
// mockRunsApi(page) answers the API and serves the runs' files, like the server would (backend/backend/api/runs.py).
export const project = 'kitefin/detector';
const qatools_config = { project: { name: project } };

// deterministic noise
const random = seed => () => {
  seed = (seed * 16807) % 2147483647;
  return (seed - 1) / 2147483646;
};

const HOUR = 3600 * 1000;
const commits = {
  c0: { id: 'c0ffee1d2e3f405162738495a6b7c8d9e0f1a2b3', branch: 'main', message: 'Backbones: add ConvNeXt-T and EfficientNet-B3', committer_name: 'Alice Martin', ago: 50 },
  c1: { id: '8a4e2f1c9b7d6e5f40312a1b2c3d4e5f60718293', branch: 'train/cosine', message: 'Cosine schedule with linear warmup', committer_name: 'Alice Martin', ago: 20 },
  c2: { id: '5d3c2b1a0f9e8d7c6b5a49382716051f2e3d4c5b', branch: 'train/cosine', message: 'Warmup length from the config', committer_name: 'Bob Chen', ago: 8 },
  c3: { id: '1f2e3d4c5b6a79880716253443526170819a2b3c', branch: 'quantize', message: 'INT8 calibration on the validation set', committer_name: 'Carol Diaz', ago: 30 },
};

// A training's curves: the loss falls (faster with a higher learning rate, until it diverges), the mAP rises
const training = ({ lr, seed = 0, steps = 2000, logged = steps, quality = 0, diverge_at = null, warmup = 0 }) => {
  const noise = random(1 + Math.round(lr * 1e6) + seed * 7919 + warmup);
  const speed = Math.min(lr, 3e-3) / 1e-3;
  const floor = 0.32 + 0.5 * Math.abs(Math.log10(lr / 1.2e-3)) ** 2 * 0.25 - quality + (seed % 10 - 1) * 0.035;
  const series = { 'train/loss': { step: [], time: [], value: [] }, 'val/loss': { step: [], time: [], value: [] }, 'val/mAP': { step: [], time: [], value: [] }, lr: { step: [], time: [], value: [] }, 'system/gpu_mem_gb': { step: [], time: [], value: [] } };
  const push = (key, s, v) => { series[key].step.push(s); series[key].time.push(s * 0.42); series[key].value.push(v); };
  for (let s = 0; s <= logged; s += 10) {
    const progress = 1 - Math.exp(-s * speed / 450);
    let loss = 2.4 - (2.4 - floor) * progress + (noise() - 0.5) * 0.18 * (1 - 0.6 * progress);
    if (diverge_at !== null && s > diverge_at) loss = s > diverge_at + 120 ? NaN : loss * (1 + (s - diverge_at) / 25);
    push('train/loss', s, Number(loss.toFixed(4)));
    const cosine = s < warmup ? lr * s / warmup : lr * 0.5 * (1 + Math.cos(Math.PI * s / steps));
    if (s % 50 === 0) push('lr', s, Number(cosine.toPrecision(4)));
    if (s % 100 === 0 && s > 0) {
      const val = loss + 0.08 + (noise() - 0.5) * 0.04 + (s > 1400 && lr > 2e-3 ? (s - 1400) / 4000 : 0);
      push('val/loss', s, Number(val.toFixed(4)));
      const map = Number.isFinite(loss) ? Math.max(0, 0.47 - floor * 0.2 - 0.25 * (1 - progress) + (noise() - 0.5) * 0.01 + quality * 0.3 - (lr > 2e-3 ? 0.02 : 0)) : NaN;
      push('val/mAP', s, Number(map.toFixed(4)));
    }
    if (s % 100 === 0) push('system/gpu_mem_gb', s, Number((10.2 + 0.4 * Math.min(1, s / 300) + noise() * 0.03).toFixed(2)));
  }
  for (const key of Object.keys(series)) series[key].points = series[key].step.length;
  return series;
};

const last = values => values.filter(Number.isFinite).at(-1);
const metrics_of = series => {
  const finite = series['val/loss'].value.filter(Number.isFinite);
  return {
    'train/loss': last(series['train/loss'].value) ?? null,
    'val/loss': finite.length ? Math.min(...finite) : null,
    'val/mAP': last(series['val/mAP'].value) ?? null,
    lr: last(series.lr.value) ?? null,
  };
};

export const make_runs = (now = Date.now()) => {
  const iso = ago_hours => new Date(now - ago_hours * HOUR).toISOString();
  const runs = [];
  let id = 1040;
  const add = ({ experiment, user, commit, configs, input = 'coco/mini-train', started, hours, running = false, failed = false, series, origin = 'local' }) => {
    id += 1;
    const label = origin === 'ci' ? experiment : `@${user}| ${experiment}`;
    runs.push({
      id, platform: 'linux', configurations: ['configs/detector.yaml', configs], extra_parameters: {},
      data: { sdk: { script: 'train.py', qaboard_version: '1.9.0' }, user, origin, ...(commit === 'c2' && user === 'bob' ? { dirty: true } : {}) },
      metrics: metrics_of(series), is_failed: failed, is_pending: running, is_running: running, deleted: false,
      created_date: iso(started).replace('Z', ''), queued_at: null, started_at: iso(started), finished_at: running ? null : iso(started - hours),
      output_dir_url: `/s/mnt/qaboard/${project}/runs/${id}`, test_input_database: '/datasets', test_input_path: input, test_input_metadata: {},
      batch: { id: 500 + Object.keys(commits).indexOf(commit), label, experiment, origin, user_name: user },
      commit: { ...commits[commit], authored_datetime: iso(commits[commit].ago) },
      _series: series,
    });
  };
  // The sweep: five learning rates, three seeds for two of them
  const sweep = [[1e-4, 0], [3e-4, 0], [1e-3, 0], [3e-3, 0], [1e-2, 0], [1e-3, 1], [1e-3, 2], [3e-4, 1], [3e-4, 2]];
  sweep.forEach(([lr, seed], index) => {
    const running = lr === 3e-4 && seed === 2;
    const diverges = lr === 1e-2;
    add({
      experiment: 'detector/lr-sweep', user: 'alice', commit: 'c1', configs: { lr, seed, batch_size: 64 },
      started: 6 - index * 0.35, hours: diverges ? 0.4 : 2.1, running, failed: diverges,
      series: training({ lr, seed, logged: running ? 1240 : diverges ? 560 : 2000, diverge_at: diverges ? 380 : null }),
    });
  });
  [0, 500].forEach((warmup, index) => add({
    experiment: 'detector/lr-sweep/warmup', user: 'bob', commit: 'c2', configs: { lr: 1e-3, seed: 0, batch_size: 64, warmup },
    started: 3 - index * 0.4, hours: 2.2, series: training({ lr: 1e-3, warmup, seed: 5 + index, quality: warmup ? 0.015 : 0 }),
  }));
  ['resnet50', 'convnext-t', 'efficientnet-b3'].forEach((backbone, index) => add({
    experiment: 'detector/backbone', user: 'alice', commit: 'c0', configs: { backbone, lr: 1e-3, batch_size: 64 },
    started: 48 - index, hours: 3.4, series: training({ lr: 1e-3, seed: index + 10, quality: [0, 0.03, 0.012][index] }),
  }));
  [256, 1024].forEach((calibration_images, index) => add({
    experiment: 'quantization/int8-calibration', user: 'carol', commit: 'c3', input: 'coco/val2017', configs: { calibration_images, precision: 'int8' },
    started: 26 - index, hours: 0.6, series: training({ lr: 3e-4, steps: 600, logged: 600, seed: index + 20, quality: index * 0.006 }),
  }));
  add({
    experiment: 'distillation/r101-teacher', user: 'bob', commit: 'c2', configs: { teacher: 'r101', lr: 5e-4, temperature: 4 },
    started: 1.2, hours: 1, running: true, series: training({ lr: 5e-4, logged: 700, seed: 30 }),
  });
  return runs;
};

const prefix_of = (experiment, node) => !node || experiment === node || experiment.startsWith(`${node}/`);
const status_of = run => run.is_running ? 'running' : run.is_pending ? 'pending' : run.is_failed ? 'failed' : 'finished';

const select = (runs, params) => runs.filter(run =>
  (params.get('scope') !== 'mine' || run.batch.user_name === 'alice')
  && (!params.get('user') || run.batch.user_name === params.get('user'))
  && (!params.get('status') || params.get('status').split(',').includes(status_of(run)))
  && (!params.get('origin') || params.get('origin').split(',').includes(run.batch.origin))
  && (!params.get('since_days') || Date.parse(run.started_at) > Date.now() - Number(params.get('since_days')) * 24 * HOUR)
);

const experiments_of = runs => {
  const rows = new Map();
  for (const run of runs) {
    const name = run.batch.experiment;
    if (!rows.has(name)) rows.set(name, { experiment: name, batches: 1, runs: 0, running: 0, pending: 0, failed: 0, latest: null, from_code: true, users: [] });
    const row = rows.get(name);
    row.runs += 1;
    row.running += run.is_running ? 1 : 0;
    row.failed += run.is_failed ? 1 : 0;
    if (!row.latest || run.started_at > row.latest) row.latest = run.started_at;
    if (!row.users.includes(run.batch.user_name)) row.users.push(run.batch.user_name);
  }
  return [...rows.values()].sort((a, b) => b.latest.localeCompare(a.latest));
};

export const mockRunsApi = async (page, { runs = make_runs(), user = 'alice', home } = {}) => {
  const public_run = ({ _series, ...run }) => run;
  const me = { is_authenticated: true, user_id: 1, user_name: user, full_name: 'Alice Martin', email: 'alice@example.com', login_type: 'local' };
  const config = home ? { ...qatools_config, project: { ...qatools_config.project, home } } : qatools_config;
  await page.addInitScript(() => localStorage.setItem('qaboard.release-notes.last-seen', '9999-12-31'));
  await page.route('**/api/v1/**', route => {
    const url = new URL(route.request().url());
    const { pathname, searchParams } = url;
    const json = body => route.fulfill({ json: body });
    if (pathname.startsWith('/api/v1/user/me')) return json(me);
    if (pathname.startsWith('/api/v1/projects')) return json({ [project]: { data: { qatools_config: config }, latest_commit_datetime: new Date().toISOString(), total_commits: 4 } });
    if (pathname === '/api/v1/project' || pathname === '/api/v1/project/') return json({ qatools_config: config, has_runs_from_code: true });
    if (pathname.startsWith('/api/v1/experiments'))
      return route.fulfill({ json: experiments_of(select(runs, searchParams)), headers: { 'X-Has-More': 'false', 'Access-Control-Expose-Headers': 'X-Has-More' } });
    if (pathname.startsWith('/api/v1/runs')) {
      const node = searchParams.get('experiment');
      const q = (searchParams.get('q') ?? '').toLowerCase();
      const found = select(runs, searchParams)
        .filter(run => prefix_of(run.batch.experiment, node))
        .filter(run => !q || q.split(/\s+/).every(word => JSON.stringify(public_run(run)).toLowerCase().replace(/[{}"]/g, '').replace(/:/g, '=').includes(word)))
        .sort((a, b) => (b.is_running - a.is_running) || b.started_at.localeCompare(a.started_at));
      const offset = Number(searchParams.get('offset') ?? 0), limit = Number(searchParams.get('limit') ?? 100);
      const page_runs = found.slice(offset, offset + limit).map(public_run);
      return route.fulfill({ json: page_runs, headers: { 'X-Has-More': String(found.length > offset + limit), 'Access-Control-Expose-Headers': 'X-Has-More' } });
    }
    const commit = Object.values(commits).find(c => pathname.startsWith(`/api/v1/commit/${c.id}`));
    if (commit) {
      const labels = [...new Set(runs.filter(run => run.commit.id === commit.id).map(run => run.batch.label))];
      return json({
        id: commit.id, hexsha: commit.id, project_id: project, branch: commit.branch, message: commit.message, committer_name: commit.committer_name,
        authored_datetime: new Date().toISOString(), data: { qatools_config: config }, parents: [], deleted: false,
        batches: Object.fromEntries(labels.map(label => [label, { label, data: {}, valid_outputs: 1, pending_outputs: 0, running_outputs: 0, failed_outputs: 0 }])),
      });
    }
    const manifest = pathname.match(/^\/api\/v1\/output\/(\d+)\/manifest/);
    if (manifest) return json({ 'series.json': { st_size: 4000, md5: manifest[1] }, 'series.jsonl': { st_size: 40000, md5: manifest[1] }, 'log.txt': { st_size: 300, md5: 'l' } });
    if (pathname.startsWith('/api/v1/commits') || pathname.startsWith('/api/v1/batches')) return json([]);
    if (pathname.startsWith('/api/v1/project/branches')) return json(['main']);
    if (pathname.startsWith('/api/v1/milestones')) return json([]);
    return json({});
  });
  await page.route('**/s/**', route => {
    const { pathname } = new URL(route.request().url());
    const run = runs.find(r => pathname.startsWith(`${r.output_dir_url}/`));
    if (!run) return route.fulfill({ status: 404 });
    if (pathname.endsWith('/series.json'))
      return route.fulfill({ json: { format: 'qaboard-series', version: 1, started_at: Date.parse(run.started_at) / 1000, series: run._series } });
    if (pathname.endsWith('/manifest.outputs.json'))
      return route.fulfill({ json: { 'series.json': { st_size: 4000, md5: String(run.id) }, 'series.jsonl': { st_size: 40000, md5: String(run.id) }, 'log.txt': { st_size: 300, md5: 'l' }, 'checkpoints/best.pt': { st_size: 98_000_000, md5: 'c' } } });
    if (pathname.endsWith('/log.txt')) return route.fulfill({ body: `step 0 loss 2.4\n...\nfinished\n` });
    return route.fulfill({ status: 404 });
  });
  return runs;
};
