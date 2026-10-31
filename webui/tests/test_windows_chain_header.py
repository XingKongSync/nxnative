import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from webui.build.windows.ios_cross_compile import chain_overlay
from webui.build.windows.windows_shader_build import publish_chain_header


REPO = Path(__file__).resolve().parents[2]
CLANG = REPO / '.webui-data/tools/ios-cross/toolchain/windows/iphone/bin/clang.exe'


class WindowsChainHeader(unittest.TestCase):
    def test_missing_header_stops_host_compilation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'run windows_shader_build.py first'):
                chain_overlay(REPO, root / 'profile', root)

    @unittest.skipUnless(CLANG.is_file(), 'Windows cross-clang is required')
    def test_host_and_packaging_use_published_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = root / 'shaders/chain/gfx/nvn/dmsh_chain.h'
            generated.parent.mkdir(parents=True)
            generated.write_text('#define DMSH_CHAIN "012345abcdef"\n', encoding='utf-8')
            profile = root / 'profile'
            publish_chain_header(root / 'shaders', profile)
            self.assertEqual((profile / 'dmsh_chain.h').read_bytes(), generated.read_bytes())
            overlay = chain_overlay(REPO, profile, root)
            self.assertEqual(json.loads(overlay.read_text())['roots'][0]['external-contents'],
                             (profile / 'dmsh_chain.h').as_posix())
            # A quoted include searches beside its source before any -I dirs.
            # Use the real NVN header's directory to catch that stale-header bug.
            source = '#include "dmsh_chain.h"\nDMSH_CHAIN\n'
            process = subprocess.run([str(CLANG), '-E', '-P', '-x', 'c',
                                      '-ivfsoverlay', str(overlay),
                                      '-iquote', str(REPO / 'dm/gfx/nvn'), '-'],
                                     input=source, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(process.stdout.strip(), '"012345abcdef"')
            self.assertEqual(process.stderr.strip(), '')


if __name__ == '__main__':
    unittest.main()
