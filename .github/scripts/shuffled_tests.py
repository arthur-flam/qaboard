#!/usr/bin/env python3
"""
Runs the Python tests (tests/) in a random order, to find tests that depend on each other, and checks that they
leave nothing in the checkout. Optionally under CPU load, to find timing-dependent tests.

It runs in the project's environment (it imports qaboard's tests), so it has no inline `# /// script` metadata:
  uv run python .github/scripts/shuffled_tests.py                      # shuffles the test cases, a random seed
  uv run python .github/scripts/shuffled_tests.py --seed 3 --repeat 3  # seeds 3, 4, 5
  uv run python .github/scripts/shuffled_tests.py --shuffle modules    # keeps each module's order
  uv run python .github/scripts/shuffled_tests.py --load 8             # with 8 busy processes alongside
  uv run python .github/scripts/shuffled_tests.py tests.test_cli tests.test_config   # some modules
  uv run python .github/scripts/shuffled_tests.py --jobs 1             # all the tests in one process
The shuffled tests are dealt to --jobs processes (default: up to 4), each runs its share in that order: most of the
tests' time is waiting (subprocesses, servers, timeouts), so 4 processes take about a quarter of the time. Each order
is still random, and the same seed and --jobs give the same orders.
A failure prints the seed: run again with it (and the same --jobs) to get the same order. Don't edit the checkout while it runs: it would
report your changes as files the tests left.

--shuffle tests moves single tests across classes and modules: unittest then runs a class's setUpClass/tearDownClass
each time the class changes, so it also checks that classes can be set up again.
"""
import argparse
import json
import multiprocessing
import os
import random
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[2]


def flatten(suite):
  for test in suite:
    if isinstance(test, unittest.TestSuite):
      yield from flatten(test)
    else:
      yield test


def ordered(tests, shuffle, rng, jobs=1):
  """
  Groups by module (or class), shuffles the groups and what's in them. With jobs > 1, deals them to that many lists:
  single tests with --shuffle tests, else whole groups (a module or class runs in one process).
  """
  if shuffle == 'tests':
    groups = [[test] for test in tests]
    rng.shuffle(groups)
  else:
    key = (lambda t: type(t).__module__) if shuffle == 'modules' else (lambda t: f'{type(t).__module__}.{type(t).__qualname__}')
    by_key = {}
    for test in tests:
      by_key.setdefault(key(test), []).append(test)
    names = list(by_key)
    rng.shuffle(names)
    if shuffle == 'classes':
      for name in names:
        rng.shuffle(by_key[name])
    groups = [by_key[name] for name in names]
  shares = [[test for group in groups[i::jobs] for test in group] for i in range(jobs)]
  return shares[0] if jobs == 1 else shares


def run_tests(tests, verbose, failfast):
  """Runs them in this order, in this process. Returns the ids of the tests that failed."""
  result = unittest.TextTestRunner(verbosity=2 if verbose else 1, failfast=failfast, buffer=True).run(unittest.TestSuite(tests))
  return [test.id() for test, _ in [*result.failures, *result.errors]] if not result.wasSuccessful() else []


def worker(ids_file, verbose, failfast):
  """--worker: runs the tests listed in the file, in its order, and writes the ids of those that failed next to it"""
  with open(ids_file) as f:
    ids = json.load(f)
  loader = unittest.TestLoader()
  tests = [loader.loadTestsFromName(test_id) for test_id in ids]
  environ = dict(os.environ)
  failed = run_tests(tests, verbose, failfast)
  changed = sorted(k for k in {*environ, *os.environ} if environ.get(k) != os.environ.get(k))
  with open(f'{ids_file}.result', 'w') as f:
    json.dump({'failed': failed, 'changed_environ': changed}, f)
  return 0


def run_in_processes(shares, args):
  """Each share in a process of its own, at the same time. Returns the failed tests and the changed variables."""
  failed, changed = [], set()
  shares = [share for share in shares if share]
  with tempfile.TemporaryDirectory(prefix='shuffled-tests-') as tmp:
    processes = []
    for i, share in enumerate(shares):
      ids_file = Path(tmp) / f'{i}.json'
      ids_file.write_text(json.dumps([test.id() for test in share]))
      command = [sys.executable, __file__, '--worker', str(ids_file), *(['-v'] if args.verbose else []), *(['--failfast'] if args.failfast else [])]
      log = open(Path(tmp) / f'{i}.log', 'w+')
      processes.append((i, len(share), ids_file, log, subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT)))
    for i, count, ids_file, log, process in processes:
      process.wait()
      log.seek(0)
      output = log.read()
      log.close()
      result_file = Path(f'{ids_file}.result')
      if result_file.exists():
        result = json.loads(result_file.read_text())
        failed += result['failed']
        changed |= set(result['changed_environ'])
      else:
        failed.append(f'(process {i} stopped with exit code {process.returncode} before it reported its results)')
      # every process's report, so that a failure's traceback shows; the passing ones' last lines only
      lines = output.rstrip().splitlines()
      ok = result_file.exists() and not json.loads(result_file.read_text())['failed']
      print(f'--- process {i + 1}/{len(processes)}: {count} tests', flush=True)
      print('\n'.join(lines[-3:] if ok else lines), flush=True)
  return failed, sorted(changed)


def checkout_files():
  """What git sees in the checkout, ignored files included (bytecode and the virtualenv aside)"""
  out = subprocess.run(['git', 'status', '--porcelain', '--ignored', '--untracked-files=all'], cwd=root, capture_output=True, text=True, check=True).stdout
  return {line for line in out.splitlines() if '__pycache__' not in line and not line[3:].startswith(('.venv/', 'node_modules/', 'webapp/', 'website/'))}


def busy(stop):
  while not stop.is_set():
    sum(i * i for i in range(10_000))


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument('modules', nargs='*', help='test modules (tests.test_cli...), default: all of tests/')
  parser.add_argument('--seed', type=int, help='default: random')
  parser.add_argument('--repeat', type=int, default=1, help='runs, with the seeds that follow')
  parser.add_argument('--shuffle', choices=['tests', 'classes', 'modules'], default='tests')
  parser.add_argument('--load', type=int, default=0, help='busy processes to run alongside')
  parser.add_argument('--jobs', type=int, default=min(4, os.cpu_count() or 1), help='processes (default: %(default)s)')
  parser.add_argument('--failfast', action='store_true')
  parser.add_argument('-v', '--verbose', action='store_true')
  parser.add_argument('--worker', help=argparse.SUPPRESS)
  args = parser.parse_args()

  os.chdir(root)
  sys.path.insert(0, str(root))
  if args.worker:
    return worker(args.worker, args.verbose, args.failfast)
  seed = args.seed if args.seed is not None else random.randrange(100_000)
  before = checkout_files()
  stop = multiprocessing.Event()
  load = [multiprocessing.Process(target=busy, args=(stop,), daemon=True) for _ in range(args.load)]
  for process in load:
    process.start()
  failed = []
  try:
    for run in range(args.repeat):
      run_seed = seed + run
      loader = unittest.TestLoader()
      suite = loader.loadTestsFromNames(args.modules) if args.modules else loader.discover('tests', top_level_dir=str(root))
      tests = list(flatten(suite))
      jobs = max(1, min(args.jobs, len(tests)))
      print(f'--- seed {run_seed}: {len(tests)} tests, --shuffle {args.shuffle}, --jobs {jobs}, --load {args.load}', flush=True)
      environ = dict(os.environ)
      start = time.monotonic()
      if jobs == 1:
        failed_tests = run_tests(ordered(tests, args.shuffle, random.Random(run_seed)), args.verbose, args.failfast)
        os.chdir(root)  # in case a test chdir'ed without restoring it
        changed = sorted(k for k in {*environ, *os.environ} if environ.get(k) != os.environ.get(k))
      else:
        failed_tests, changed = run_in_processes(ordered(tests, args.shuffle, random.Random(run_seed), jobs), args)
      print(f'--- seed {run_seed}: {time.monotonic() - start:.0f}s', flush=True)
      if failed_tests:
        failed.append(run_seed)
        for test_id in failed_tests:
          print(f'FAILED (seed {run_seed}, --jobs {jobs}): {test_id}')
      if changed:
        print(f'The tests changed environment variables that later tests see (seed {run_seed}): {", ".join(changed)}')
      left = sorted(checkout_files() - before)
      if left:
        failed.append(run_seed)
        print(f'The tests left files in the checkout (seed {run_seed}), use a temporary directory:', *left, sep='\n  ')
        before |= set(left)
  finally:
    stop.set()
  if failed:
    print(f'Failed with the seeds: {sorted(set(failed))} (run again with --seed N --jobs {args.jobs})')
  return 1 if failed else 0


if __name__ == '__main__':
  sys.exit(main())
