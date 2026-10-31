import hashlib
from pathlib import Path
import tempfile
import unittest

from webui import staging

REPO = Path(__file__).resolve().parents[2]

TARGETS = {
    'dm/gfx/tools/shader_split.py',
    'dm/ios/tools/gen_embed.py',
    'dm/host/hle_count.c',
    'dm/tools/nx_extract/Program.cs',
    'dm/host/Makefile',
    'dm/gfx/nvn/nvn_mtl.m',
    'dm/ios/Sources/main.m',
    'dm/ios/tools/build_ipa.sh',
    'scripts/build_ipa_from_nsp.py',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class StagingTests(unittest.TestCase):
    def test_linked_worktree_git_file_is_not_copied(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.git').write_text('gitdir: /tmp/worktree\n', encoding='utf-8')
            (root / 'file.txt').write_text('x', encoding='utf-8')
            files = {path.relative_to(root).as_posix() for path in staging.iter_files(root)}
            self.assertNotIn('.git', files)
            self.assertIn('file.txt', files)

    def test_launcher_variants_differ_only_in_the_launcher_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            objc = staging.ensure(REPO, root, launcher='objc')
            swift = staging.ensure(REPO, root, launcher='swiftui')
            self.assertNotEqual(objc, swift)
            self.assertTrue((objc / 'dm/ios/Sources/DmLauncher.m').is_file())
            self.assertTrue((swift / 'dm/ios/Sources/Launcher.swift').is_file())
            self.assertFalse((swift / 'dm/ios/Sources/DmLauncher.m').exists())
            for name in staging.OBJC_REMOVED:
                self.assertFalse((objc / name).exists(), name)
                self.assertTrue((swift / name).is_file(), name)
            # everything else, patched scripts included, is identical
            for tree in (objc, swift):
                self.assertIn('@NXEVENT', (tree / 'scripts/build_ipa_from_nsp.py').read_text(encoding='utf-8'))
                self.assertEqual(digest(tree / 'dm/ios/Sources/main.m'), digest(REPO / 'dm/ios/Sources/main.m'))
                staging.verify(tree)
            self.assertEqual(swift, staging.ensure(REPO, root, launcher='swiftui'))   # reused per launcher
            with self.assertRaises(ValueError):
                staging.compute_key(REPO, launcher='nope')

    def test_stage_is_isolated_and_reused(self):
        before = {target: digest(REPO / target) for target in TARGETS}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tree = staging.ensure(REPO, root, force=True)
            self.assertIn('@NXEVENT', (tree / 'scripts/build_ipa_from_nsp.py').read_text(encoding='utf-8'))
            self.assertIn('--separate-romfs',
                          (tree / 'scripts/build_ipa_from_nsp.py').read_text(encoding='utf-8'))
            self.assertEqual(digest(tree / 'dm/ios/Sources/main.m'), digest(REPO / 'dm/ios/Sources/main.m'))   # upstream's launcher: main.m is not patched
            import webui.build.romfs_archive  # staged helper is importable
            self.assertEqual(tree, staging.ensure(REPO, root))
            staging.verify(tree)
            (tree / 'dm/ios/Sources/main.m').write_text('mutated', encoding='utf-8')
            self.assertEqual(before['dm/ios/Sources/main.m'],
                             digest(REPO / 'dm/ios/Sources/main.m'))
            for target, value in before.items():
                self.assertEqual(value, digest(REPO / target), target)


if __name__ == '__main__':
    unittest.main()
