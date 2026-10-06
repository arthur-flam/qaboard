#!/usr/bin/env python3
"""
Creates a git repository with the denoiser project and a believable history, as it was before QA-Board:
no qa/ folder, no qaboard.yaml, and messy results_final_v3_REAL/ folders in the working tree.

  python make_history.py ~/denoiser               # commits 1-3 (stops at the commit that regresses fabric)
  python make_history.py ~/denoiser --upto 2      # stops earlier, to make the next commits later
  python make_history.py ~/denoiser --apply-next  # adds the next commit (dated now, unless --date)
  python make_history.py ~/denoiser --apply-next --no-commit   # only changes the files: commit them yourself
  python make_history.py --list

Needs git, numpy and Pillow. The content is deterministic.
"""
import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

HERE = Path(__file__).resolve().parent
PROJECT = HERE / 'project'
HISTORY = HERE / 'history'
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

AUTHOR_NAME = 'Noa Levi'
AUTHOR_EMAIL = 'noa@example.com'
# Not reachable: only used by `qa wizard` to name the project (imaging/denoiser)
REMOTE = 'git@gitlab.example.com:imaging/denoiser.git'


@dataclass
class Commit:
  subject: str
  body: str
  date: str
  files: Dict[str, Path] = field(default_factory=dict)  # path in the repo: source file
  data: bool = False                                      # whether it adds the test images


BASE_FILES = ['README.md', 'requirements.txt', '.gitignore', 'denoise.py',
              'denoiser/__init__.py', 'denoiser/io.py', 'denoiser/metrics.py']

COMMITS = [
  Commit(
    "Gaussian denoiser + eval script",
    "denoise.py runs a filter on an image, and prints PSNR/SSIM when\n"
    "data/clean/ has the ground truth. Test images in data/.",
    '2026-09-08T10:24:00+03:00',
    {**{f: PROJECT / f for f in BASE_FILES}, 'denoiser/filters.py': HISTORY / 'filters_1.py'},
    data=True,
  ),
  Commit(
    "Bilateral filter: keep edges sharp",
    "The gaussian blurs text and fabric. The bilateral filter only averages\n"
    "pixels with similar colors. It's the new default.\n\n"
    "street: 28.6 -> 35.1 dB\nsign:   23.5 -> 33.2 dB",
    '2026-09-15T16:02:00+03:00',
    {'denoiser/filters.py': HISTORY / 'filters_2.py'},
  ),
  Commit(
    "Stronger smoothing for night shots",
    "Night shots stayed very noisy with the bilateral filter. Estimate the\n"
    "noise level and smooth more when it's high.\n\n"
    "parking: 20.7 -> 25.9 dB\nneon:    20.7 -> 24.7 dB",
    '2026-09-24T19:41:00+03:00',
    {'denoiser/filters.py': HISTORY / 'filters_3.py'},
  ),
  Commit(
    "Estimate noise on diagonal details: fabric isn't noise",
    "The laplacian sees fine stripes as noise, so we smoothed fabric way too\n"
    "much. Use the median of the diagonal Haar details (Donoho) instead:\n"
    "horizontal and vertical structures cancel out there.",
    '2026-10-01T11:15:00+03:00',
    {'denoiser/filters.py': PROJECT / 'denoiser' / 'filters.py'},
  ),
]
DEFAULT_UPTO = 3


# What Noa tried by hand after each commit: (folder, image, extra denoise.py args, output name)
EXPERIMENTS = {
  1: [
    ('results', 'day/street', [], 'street_denoised.png'),
    ('results', 'text/sign', [], 'sign_denoised.png'),
    ('results', 'night/parking', [], 'parking_denoised.png'),
  ],
  2: [
    ('results_final', 'day/street', [], 'street.png'),
    ('results_final', 'text/sign', [], 'sign.png'),
    ('results_final', 'texture/fabric', [], 'fabric.png'),
    ('results_final', 'night/parking', [], 'parking.png'),
    ('results_final_v2', 'night/parking', ['--strength', '1.5'], 'parking_s1.5.png'),
    ('results_final_v2', 'night/neon', ['--strength', '1.5'], 'neon_s1.5.png'),
    ('results_final_v2', 'texture/fabric', ['--strength', '1.5'], 'fabric_s1.5.png'),
    ('results_thursday_GOOD?', 'day/portrait', ['--strength', '0.8'], 'portrait.png'),
    ('results_thursday_GOOD?', 'text/sign', ['--strength', '0.8'], 'sign_08.png'),
    ('results_thursday_GOOD?', 'night/neon', ['--method', 'gaussian'], 'neon_gauss.png'),
    ('results_final_v3_REAL', 'day/street', ['--strength', '1.2'], 'street_bilat.png'),
    ('results_final_v3_REAL', 'day/street', ['--method', 'gaussian'], 'street_gauss.png'),
    ('results_final_v3_REAL', 'night/parking', ['--method', 'gaussian', '--strength', '1.3'], 'parking_FINAL.png'),
    ('results_final_v3_REAL', 'sky/sunset', [], 'sunset.png'),
  ],
  3: [
    ('results_final_v3_REAL', 'night/parking', [], 'parking_nightfix.png'),
    ('results_final_v3_REAL', 'night/neon', [], 'neon_nightfix.png'),
  ],
}

NOTES = """\
denoiser - notes (don't commit)

sept 8
- gaussian sigma=1 ok on the street, text on the sign is blurry
- results/ = gaussian

sept 15
- bilateral!! sign is SO much sharper (results_final/)
- night still super noisy with bilateral, gaussian looks better there??
- tried strength 1.5 on night -> results_final_v2/  (fabric looks like mush? or was it in v3)
- results_thursday_GOOD? = strength 0.8, portrait looked nice. which commit was that??
- results_final_v3_REAL: compare gaussian vs bilateral on street. parking_FINAL is gaussian 1.3

sept 24
- night fix: estimate noise, smooth more if noisy. parking 20.7 -> 25.9 !!
- didn't re-run everything, it takes forever by hand. TODO re-run all images
- TODO copy the numbers to results_table.csv
- TODO ask in the team channel how they track this stuff
"""


def run(cmd, cwd, env=None, capture=False):
  result = subprocess.run(cmd, cwd=cwd, env=env, check=True, text=True, capture_output=capture)
  return result.stdout if capture else None


def git(target: Path, *args, env=None, capture=False):
  return run(['git', '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', *args], target, env=env, capture=capture)


def apply_files(target: Path, commit: Commit):
  for rel, source in commit.files.items():
    dest = target / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
  if commit.data:
    from make_dataset import make_dataset
    make_dataset(target / 'data', verbose=False)


def make_commit(target: Path, commit: Commit, date: Optional[str] = None):
  paths = list(commit.files) + (['data'] if commit.data else [])
  git(target, 'add', '--', *paths)
  date = date or commit.date
  env = {**os.environ,
         'GIT_AUTHOR_NAME': AUTHOR_NAME, 'GIT_AUTHOR_EMAIL': AUTHOR_EMAIL, 'GIT_AUTHOR_DATE': date,
         'GIT_COMMITTER_NAME': AUTHOR_NAME, 'GIT_COMMITTER_EMAIL': AUTHOR_EMAIL, 'GIT_COMMITTER_DATE': date}
  git(target, 'commit', '-q', '-m', f"{commit.subject}\n\n{commit.body}", env=env)


def experiments(target: Path, stage: int, rows: list):
  """Runs denoise.py by hand, like Noa did, and keeps what it printed for the 'hand-copied' table."""
  env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
  for folder, image, args, name in EXPERIMENTS.get(stage, []):
    out = f"{folder}/{name}"
    printed = run([sys.executable, 'denoise.py', f'data/noisy/{image}.png', '--output', out, *args], target, env=env, capture=True)
    method = args[args.index('--method') + 1] if '--method' in args else ('gaussian' if stage == 1 else 'bilateral')
    strength = args[args.index('--strength') + 1] if '--strength' in args else '1'
    match = re.search(r'PSNR ([\d.]+) dB.*SSIM ([\d.]+)', printed)
    rows.append((stage, folder, image, method, strength, match.group(1) if match else '', match.group(2) if match else ''))


DATES = {1: '9/8', 2: '9/15', 3: '9/24'}


def write_table(target: Path, rows: list):
  """A spreadsheet with numbers copied by hand: rounded, some missing, some notes."""
  with open(target / 'results_table.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['date', 'image', 'method', 'strength', 'PSNR', 'SSIM', 'folder', 'comment'])
    for i, (stage, folder, image, method, strength, psnr, ssim) in enumerate(rows):
      psnr = f"{float(psnr):.1f}" if psnr else ''
      ssim = '' if i % 3 == 2 else (f"{float(ssim):.2f}" if ssim else '')  # forgot some
      comment = 'mush??' if image == 'texture/fabric' and strength == '1.5' else ''
      if image.startswith('night/') and psnr and float(psnr) < 24:
        comment = 'still noisy'
      if folder == 'results_thursday_GOOD?':
        comment = 'GOOD? which commit'
      writer.writerow([DATES[stage], image.split('/')[-1], method, strength, psnr, ssim, folder + '/', comment])
    writer.writerow(['', 'fabric', 'bilateral', '1', '', '', '', 'TODO re-run after night fix'])


def create(target: Path, upto: int, force: bool):
  if target.exists() and any(target.iterdir()):
    if not force:
      sys.exit(f"{target} exists and isn't empty, use --force to replace it")
    if not (target / '.git').is_dir() or not (target / 'denoise.py').is_file():
      sys.exit(f"{target} doesn't look like a repository made by this script, not deleting it")
    shutil.rmtree(target)
  target.mkdir(parents=True, exist_ok=True)
  git(target, 'init', '-q', '-b', 'main')
  git(target, 'remote', 'add', 'origin', REMOTE)
  rows: list = []
  for stage, commit in enumerate(COMMITS[:upto], start=1):
    apply_files(target, commit)
    make_commit(target, commit)
    print(f"[{stage}] {commit.subject}")
    experiments(target, stage, rows)
  if upto >= 2:
    (target / 'notes.txt').write_text(NOTES if upto >= 3 else NOTES.split('\nsept 24')[0] + '\n')
  if rows:
    write_table(target, rows)


def next_commit(target: Path) -> Optional[int]:
  subjects = set(git(target, 'log', '--format=%s', capture=True).splitlines())
  for i, commit in enumerate(COMMITS):
    if commit.subject not in subjects:
      return i
  return None


def apply_next(target: Path, commit_it: bool, date: Optional[str]):
  if not (target / '.git').is_dir():
    sys.exit(f"{target} isn't a git repository: create it first with `make_history.py {target}`")
  i = next_commit(target)
  if i is None:
    sys.exit("All the commits are already there.")
  commit = COMMITS[i]
  apply_files(target, commit)
  if commit_it:
    make_commit(target, commit, date or datetime.now().astimezone().isoformat(timespec='seconds'))
    print(f"[{i + 1}] {commit.subject}")
  else:
    print(f"Changed {', '.join(commit.files)} for commit {i + 1}. To commit it:")
    print(f"  git commit -am \"{commit.subject}\"")


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument('target', type=Path, nargs='?')
  parser.add_argument('--upto', type=int, default=DEFAULT_UPTO, choices=range(1, len(COMMITS) + 1), help=f"last commit to create (default: {DEFAULT_UPTO})")
  parser.add_argument('--force', action='store_true', help="replace the target if this script created it")
  parser.add_argument('--apply-next', action='store_true', help="add the next commit to an existing target")
  parser.add_argument('--no-commit', action='store_true', help="with --apply-next: only change the files")
  parser.add_argument('--date', help="with --apply-next: commit date (ISO 8601), default: now")
  parser.add_argument('--list', action='store_true', help="list the commits")
  args = parser.parse_args()
  if args.list:
    for i, commit in enumerate(COMMITS, start=1):
      print(f"{i}. {commit.date[:10]}  {commit.subject}")
    return
  if not args.target:
    parser.error("give a target folder")
  target = args.target.expanduser().resolve()
  if args.apply_next:
    apply_next(target, not args.no_commit, args.date)
  else:
    create(target, args.upto, args.force)


if __name__ == '__main__':
  main()
