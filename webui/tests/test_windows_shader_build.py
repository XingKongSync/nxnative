import ast
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

from webui.build.windows import windows_shader_build as adapter
from webui.file_tools import remove_stage


def prepare_tool(command, **kwargs):
    if command[-1] == '--header':
        with patch.dict(os.environ, kwargs['env']), patch.object(sys, 'argv', command[1:]):
            runpy.run_path(command[1], run_name='__main__')


class WindowsShaderPreparation(unittest.TestCase):
    def test_prepare_in_real_task_depth_beyond_max_path(self):
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Match the real task layout, whose longest vendored source paths
            # exceed 260 characters even though the output root does not.
            task = root / 'repo01' / '.webui-data/tasks' / ('a' * 32)
            out = task / 'inputs/0100A00019DE0000_build/windows_shaders'
            relative = Path('src/Ryujinx.Common/Configuration/Hid/Controller/Motion/JsonMotionConfigControllerConverter.cs')
            copied = out / 'chain/third_party/ryujinx' / relative
            self.assertGreater(len(str(copied)), 260)
            metal = root / 'metal.exe'
            for tool in (metal, root / 'metallib.exe', root / 'spirv-cross.exe'):
                tool.write_bytes(b'fixture compiler')
            argv = ['windows_shader_build.py', '--profile', str(root / 'profile'),
                    '--packages', str(root / 'packages'), '--out', str(out),
                    '--metal', str(metal), '--spirv-cross', str(root / 'spirv-cross.exe'),
                    '--dotnet', str(root / 'dotnet.exe'), '--prepare-only']
            try:
                with patch.object(sys, 'argv', argv), patch.object(adapter.subprocess, 'run', side_effect=prepare_tool) as run:
                    adapter.main()
                self.assertEqual(Path(adapter.filesystem_path(copied)).read_bytes(),
                                 (repo / 'dm/third_party/ryujinx' / relative).read_bytes())
                self.assertEqual(run.call_count, 4)
                # Prefixes belong to Python file IO, not compiler arguments.
                for call in run.call_args_list:
                    self.assertFalse(any(str(arg).startswith('\\\\?\\') for arg in call.args[0]))
            finally:
                remove_stage(root / 'repo01', root)

    def test_prepare_current_upstream_chain(self):
        # Exercise all source transformations against the actual upstream sources;
        # only external compiler execution is mocked (no game files required).
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metal = root / 'Metal tools/metal.exe'
            metal.parent.mkdir()
            for tool in (metal, metal.with_name('metallib.exe'), root / 'spirv-cross.exe'):
                tool.write_bytes(b'fixture compiler')
            out = root / 'out'
            argv = ['windows_shader_build.py', '--profile', str(root / 'profile'),
                    '--packages', str(root / 'packages'), '--out', str(out),
                    '--metal', str(metal), '--spirv-cross', str(root / 'spirv-cross.exe'),
                    '--dotnet', str(root / 'dotnet.exe'), '--prepare-only']
            with patch.object(sys, 'argv', argv), patch.object(adapter.subprocess, 'run', side_effect=prepare_tool) as run:
                adapter.main()
            self.assertEqual(run.call_count, 4)
            self.assertEqual(run.call_args_list[0].args[0][1], 'publish')
            self.assertEqual([call.args[0][-1] for call in run.call_args_list[1:]],
                             ['--update-lock', '--check-tools', '--header'])
            env = run.call_args.kwargs['env']
            self.assertEqual(env['SPIRV_CROSS'], str(root / 'spirv-cross.exe'))
            self.assertEqual(Path(env['XLATE']).parent, out / 'chain/gfx/tools/dmsh_xlate_bin')
            sources = out / 'chain/gfx/tools'
            for name in ('c1_guard.py', 'cb_extent.py'):
                self.assertEqual((sources / name).read_bytes(), (repo / 'dm/gfx/tools' / name).read_bytes())
            for source in sources.glob('*.py'):
                ast.parse(source.read_text(encoding='utf-8'))
            build = ast.parse((sources / 'dmsh_build.py').read_text(encoding='utf-8'))
            mac = next(ast.literal_eval(node.value) for node in build.body
                       if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == 'MAC_METAL' for t in node.targets))
            self.assertEqual(mac[:3], [str(metal), '-target', 'air64-apple-macos14.0'])
            for flag in ('-c', '-O1', '-fpreserve-invariance'):
                self.assertIn(flag, mac)
            ios = runpy.run_path(str(out / 'chain/ios/tools/ios_metal.py'))
            for flag in ('-O1', '-fpreserve-invariance', '-fmetal-math-mode=relaxed', 'air64-apple-ios18.0'):
                self.assertIn(flag, ios['METAL'])
            self.assertEqual(ios['RECORD'], ' '.join(ios['METAL']) + '; ' + ' '.join(ios['METALLIB']))
            self.assertFalse(any(flag.startswith('-mios-version-min=') for flag in ios['METAL']))
            with patch.dict(os.environ, env):
                chain = runpy.run_path(str(sources / 'dmsh_chain.py'))
                version = chain['version']()
            self.assertEqual(len(version), 12)
            self.assertTrue((out / 'chain/gfx/nvn').is_dir())
            self.assertEqual((root / 'profile/dmsh_chain.h').read_bytes(),
                             (out / 'chain/gfx/nvn/dmsh_chain.h').read_bytes())
            self.assertEqual((sources / 'dmsh_pull.metal').read_bytes(),
                             (repo / 'dm/gfx/tools/dmsh_pull.metal').read_bytes())

    def test_future_compile_flags_and_multiline_assignment_are_preserved(self):
        source = "METAL = [\n 'xcrun', '-sdk', 'iphoneos', 'metal', '-c',\n '-O1', '-new-flag', '-mios-version-min=19.0'\n]\n"
        result = adapter.metal_command(source, 'METAL', 'metal.exe', 'air64-apple-ios18.0', 'cache')
        command = ast.literal_eval(ast.parse(result).body[0].value)
        self.assertIn('-new-flag', command)
        self.assertIn('air64-apple-ios19.0', command)

    def test_unrecognized_command_fails_instead_of_dropping_flags(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported upstream Metal command'):
            adapter.metal_command("METAL = ['other-compiler', '-c']", 'METAL', 'metal.exe', 'target', 'cache')


if __name__ == '__main__':
    unittest.main()
