"""End-to-end runs of webui/tools/sync_upstream.py against a throwaway upstream and fork.

The upstream is a tiny local repository; the fork is that repository plus a copy of the
fork's own tooling, one hook patch, a fork-owned file and the fork block of .gitattributes.
`NX_UPSTREAM_URL` points the tool at the local upstream.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
GIT = shutil.which('git')

UPSTREAM_ATTRIBUTES = 'docs/gallery.tsv merge=union\n'
FORK_ATTRIBUTES = ('\n# fork block\n.gitattributes merge=union\n'
                   'start-webui.sh text eol=lf\n')
PATCH = '''diff --git a/dm/tool.py b/dm/tool.py
--- a/dm/tool.py
+++ b/dm/tool.py
@@ -1,4 +1,5 @@
 one = 1
 two = 2
+hook = True
 three = 3
 four = 4
'''
TRIVIAL_TEST = 'import unittest\n\nclass Trivial(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n'


def run(*args, cwd, env=None, check=True):
    process = subprocess.run([str(x) for x in args], cwd=cwd, capture_output=True, text=True, env=env)
    if check and process.returncode:
        raise AssertionError(f'{args} failed:\n{process.stdout}\n{process.stderr}')
    return process


@unittest.skipUnless(GIT, 'git is required')
class SyncEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.upstream, self.fork = root / 'upstream', root / 'fork'
        self.env = dict(os.environ, NX_UPSTREAM_URL=str(self.upstream), GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid',
                        GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid', PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        self.env.pop('GIT_DIR', None)
        self.upstream.mkdir()
        self.write(self.upstream, 'dm/tool.py', 'one = 1\ntwo = 2\nthree = 3\nfour = 4\n')
        self.write(self.upstream, 'docs/readme.md', 'docs\n')
        self.write(self.upstream, 'dm/ios/project.yml', 'UILaunchScreen: {}\nUIRequiresFullScreen: true\n')   # what staging.verify reads
        self.write(self.upstream, '.gitattributes', UPSTREAM_ATTRIBUTES)
        self.git(self.upstream, 'init', '-q', '-b', 'main')
        self.commit(self.upstream, 'upstream 1')
        self.git(root, 'clone', '-q', str(self.upstream), str(self.fork))
        self.git(self.fork, 'remote', 'add', 'upstream', str(self.upstream))
        self.git(self.fork, 'fetch', '-q', 'upstream')
        self.make_fork()
        self.git(self.fork, 'update-ref', 'refs/remotes/upstream/main', self.git(self.upstream, 'rev-parse', 'HEAD').stdout.strip())

    # ---- helpers ----
    def git(self, cwd, *args, check=True):
        return run('git', *args, cwd=cwd, env=self.env, check=check)

    def write(self, root, name, text):
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')

    def commit(self, root, message):
        self.git(root, 'add', '-A')
        self.git(root, 'commit', '-q', '-m', message)

    def make_fork(self):
        shutil.copytree(REPO / 'webui' / 'tools', self.fork / 'webui' / 'tools',
                        ignore=shutil.ignore_patterns('__pycache__'), dirs_exist_ok=True)
        for name in ('__init__.py', 'diffapply.py', 'staging.py', 'repo.py'):
            shutil.copy2(REPO / 'webui' / name, self.fork / 'webui' / name)
        shutil.copytree(REPO / 'webui' / 'overlays', self.fork / 'webui' / 'overlays', dirs_exist_ok=True)
        self.write(self.fork, 'webui/tests/__init__.py', '')
        self.write(self.fork, 'webui/tests/test_trivial.py', TRIVIAL_TEST)
        self.write(self.fork, 'webui/patches/0001-hook.patch', PATCH)
        self.write(self.fork, 'webui/patches/series', '# <patch file> <target>\n# Base: x\n# Fork ref: x\n0001-hook.patch dm/tool.py\n')
        self.write(self.fork, 'FORK.md', 'fork\n')
        base = self.git(self.upstream, 'rev-parse', 'HEAD').stdout.strip()
        self.write(self.fork, 'webui/patches/UPSTREAM_BASE',
                   f'base {base}\nupstream {base}\nfork {base}\nhooks {base}\nmerge_base {base}\n')
        with open(self.fork / '.gitattributes', 'a', encoding='utf-8', newline='\n') as stream:
            stream.write(FORK_ATTRIBUTES)
        self.commit(self.fork, 'fork')

    def sync(self):
        process = run(sys.executable, 'webui/tools/sync_upstream.py', '--mode', 'review', '--json',
                      cwd=self.fork, env=self.env, check=False)
        report = self.fork / '.webui-data/upstream-sync/report/result.json'
        self.assertTrue(report.is_file(), process.stdout + process.stderr)
        return json.loads(report.read_text(encoding='utf-8')), process

    def advance_upstream(self, files, message='upstream 2'):
        for name, text in files.items():
            self.write(self.upstream, name, text)
        self.commit(self.upstream, message)
        self.git(self.fork, 'fetch', '-q', 'upstream')

    # ---- scenarios ----
    def test_up_to_date(self):
        result, _ = self.sync()
        self.assertTrue(result['ok'])
        self.assertFalse(result['changed'])
        self.assertEqual(result['stage'], 'up-to-date')

    def test_clean_upstream_change_is_merged_and_patches_regenerated(self):
        self.advance_upstream({'docs/readme.md': 'docs v2\n', 'dm/new.py': 'x = 1\n'})
        before = self.git(self.fork, 'rev-parse', 'main').stdout.strip()
        result, process = self.sync()
        self.assertEqual((result['stage'], result['ok']), ('synced', True), process.stdout + process.stderr)
        self.assertTrue(result['changed'])
        sha = result['sha']
        upstream = self.git(self.upstream, 'rev-parse', 'HEAD').stdout.strip()
        merge = self.git(self.fork, 'rev-list', '--parents', '-n', '1', sha + '^').stdout.split()
        self.assertEqual(set(merge[1:]), {before, upstream})   # the patch commit sits on a merge of fork and upstream
        merged = self.git(self.fork, 'show', f'{sha}:docs/readme.md').stdout
        self.assertEqual(merged, 'docs v2\n')
        base = self.git(self.fork, 'show', f'{sha}:webui/patches/UPSTREAM_BASE').stdout
        self.assertIn('upstream ' + self.git(self.upstream, 'rev-parse', 'HEAD').stdout.strip(), base)
        self.assertEqual(self.git(self.fork, 'rev-parse', 'main').stdout.strip(), before)   # main itself never moves
        self.assertEqual(self.git(self.fork, 'branch', '--list', 'review/upstream-sync').stdout.split()[-1],
                         'review/upstream-sync')

    def test_upstream_edit_of_gitattributes_does_not_stop_the_sync(self):
        self.advance_upstream({'.gitattributes': UPSTREAM_ATTRIBUTES + 'docs/other.tsv merge=union\n'})
        result, process = self.sync()
        self.assertEqual((result['stage'], result['ok']), ('synced', True), process.stdout + process.stderr)
        text = self.git(self.fork, 'show', result['sha'] + ':.gitattributes').stdout
        self.assertIn('docs/other.tsv merge=union', text)
        self.assertIn('start-webui.sh text eol=lf', text)

    def test_patch_that_no_longer_applies_stops_at_patch_apply(self):
        self.advance_upstream({'dm/tool.py': 'one = 100\ntwo = 200\nthree = 300\nfour = 400\n'})
        result, _ = self.sync()
        self.assertFalse(result['ok'])
        self.assertEqual(result['stage'], 'patch_apply')

    def test_upstream_change_to_fork_owned_path_stops_before_merging(self):
        self.advance_upstream({'FORK.md': 'upstream wrote this\n'})
        result, _ = self.sync()
        self.assertFalse(result['ok'])
        self.assertEqual(result['stage'], 'fork-owned-change')
        self.assertEqual(self.git(self.fork, 'branch', '--list', 'review/upstream-sync').stdout.strip(), '')


if __name__ == '__main__':
    unittest.main()
