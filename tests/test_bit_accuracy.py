"""
Bit-accuracy of text files across platforms: file_info normalizes their end-of-lines,
so that a CRLF checkout (Windows) records the same md5/st_size as a LF one (Linux).
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def file_info(*args, **kwargs):
  # Importing qaboard resolves qaboard.yaml from the current directory: import it late (see tests/test_git.py)
  from qaboard.utils import file_info
  return file_info(*args, **kwargs)


CRLF_CONTENT = b'line one\r\nline two\r\nline three\r\n'
LF_CONTENT = CRLF_CONTENT.replace(b'\r\n', b'\n')


class TestFileEolNormalization(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)

  def write(self, name, content):
    path = Path(self.tmp.name) / name
    path.write_bytes(content)
    return path

  def assertRecorded(self, info, content):
    self.assertEqual(info['md5'], hashlib.md5(content).hexdigest())
    self.assertEqual(info['st_size'], len(content))

  def test_crlf(self):
    self.assertRecorded(file_info(self.write('noise.txt', CRLF_CONTENT)), LF_CONTENT)

  def test_lone_cr(self):
    self.assertRecorded(file_info(self.write('noise.txt', b'a\rb\rc\r')), b'a\nb\nc\n')

  def test_invalid_utf8_bytes_are_kept(self):
    crlf = b'caf\xe9\r\n\xff\xfe\r\n'
    self.assertRecorded(file_info(self.write('latin1.txt', crlf)), crlf.replace(b'\r\n', b'\n'))

  def test_non_plaintext_files_are_not_normalized(self):
    self.assertRecorded(file_info(self.write('noise.bin', CRLF_CONTENT)), CRLF_CONTENT)

  def test_binary_looking_plaintext_is_not_normalized(self):
    # With bit_accuracy.binary, every other file counts as plaintext: files with NUL bytes stay raw
    content = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
    config = {'bit_accuracy': {'binary': ['*.raw']}}
    self.assertRecorded(file_info(self.write('image.png', content), config=config), content)

  def test_normalize_eof_false(self):
    self.assertRecorded(file_info(self.write('noise.txt', CRLF_CONTENT), normalize_eof=False), CRLF_CONTENT)

  def test_compute_hashes_false(self):
    info = file_info(self.write('noise.txt', CRLF_CONTENT), compute_hashes=False)
    self.assertEqual(info, {'st_size': len(LF_CONTENT)})

  def test_temporary_file_is_removed(self):
    with tempfile.TemporaryDirectory() as tmp, mock.patch('tempfile.tempdir', tmp):
      file_info(self.write('noise.txt', CRLF_CONTENT))
      self.assertEqual(list(Path(tmp).iterdir()), [])

  def test_crlf_and_lf_manifests_match(self):
    from qaboard.bit_accuracy import cmpmanifests
    tmp = Path(self.tmp.name)
    for name, content in (('windows', CRLF_CONTENT), ('linux', LF_CONTENT)):
      path = self.write(f'{name}.txt', content)
      (tmp / f'manifest.{name}.json').write_text(json.dumps({'noise.txt': file_info(path)}))
    comparison = cmpmanifests(tmp / 'manifest.linux.json', tmp / 'manifest.windows.json')
    self.assertEqual(comparison['match'], {Path('noise.txt')})


if __name__ == '__main__':
  unittest.main()
