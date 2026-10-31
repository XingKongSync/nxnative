import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from webui import worker
from webui.build.romfs_archive import archive_path

TITLE = '0100A00019DE0000'


class WorkerSeparateRomfsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / 'task'
        self.folder.mkdir()
        self.source = self.root / 'game.nsp'
        self.source.write_bytes(b'nsp')
        self.keys = self.root / 'prod.keys'
        self.keys.write_bytes(b'keys')
        tools = self.root / 'tools'
        self.extractor = tools / 'nx_extract' / ('nx_extract.exe' if os.name == 'nt' else 'nx_extract')
        self.extractor.parent.mkdir(parents=True)
        self.extractor.write_bytes(b'extractor')
        self.stage = self.root / 'fake-stage'
        self.stage.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def task(self, targets=('ios',), separate=False, settings=None, launcher=None):
        value = dict(kind='build', files=[str(self.source)], keys=str(self.keys), game=TITLE,
                     targets=list(targets), settings=settings or {}, separate_romfs=separate)
        if launcher: value['launcher'] = launcher
        (self.folder / 'task.json').write_text(json.dumps(value), encoding='utf-8')
        return value

    def run_build(self, targets=('ios',), separate=False, system='Windows', output=None, settings=None, launcher=None):
        task = self.task(targets, separate, settings, launcher)
        commands = []
        marker = worker.game.load(TITLE).get('romfs_marker')

        def fake_run(folder, command, env=None, cwd=None):
            command = [str(x) for x in command]
            commands.append(command)
            if command[0] == 'ditto':   # the Mac app folder archived as .app.zip
                with zipfile.ZipFile(command[-1], 'w') as archive:
                    archive.writestr('Game.app/Contents/MacOS/Launch', b'x')
                return
            output = Path(command[command.index('--out') + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            if '--platform' in command and command[command.index('--platform') + 1] == 'mac-app':
                (output / 'Contents').mkdir(parents=True, exist_ok=True)
                return
            with zipfile.ZipFile(output, 'w') as archive:
                archive.writestr('Payload/DmIOS.app/DmIOS', b'x')
            if '--separate-romfs' in command:
                with zipfile.ZipFile(archive_path(output), 'w') as archive:
                    archive.writestr('romfs/' + marker, b'marker')

        config = {'tools': str(self.root / 'tools')}
        if output is not None: config['output'] = str(output)
        profiles = [{'id': TITLE, 'graphics': 'nvn', 'ios': True, 'mac': True, 'reason': ''}]
        with patch.dict(os.environ, NX_WEB_CONFIG=json.dumps(config), NX_STAGE_ROOT=str(self.root / 'stage-root')), \
             patch.object(worker.platform, 'system', return_value=system), \
             patch.object(worker, 'profiles', return_value=profiles), \
             patch.object(worker, 'entries', return_value=[]), \
             patch.object(worker, 'catalog', side_effect=lambda extractor, keys, path: [dict(title_id=TITLE, version=0, type=0x80)]), \
             patch.object(worker.staging, 'ensure', return_value=self.stage) as ensure, \
             patch.object(worker, 'run', side_effect=fake_run):
            self.ensure = ensure
            if separate and 'ios' not in targets:
                with self.assertRaises(ValueError):
                    worker.execute(self.folder)
                return [], []
            worker.execute(self.folder)
        events = [json.loads(line) for line in (self.folder / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
        artifacts = [event['name'] for event in events if event['kind'] == 'artifact']
        return artifacts, commands

    def test_separate_ios_emits_archive(self):
        artifacts, commands = self.run_build(separate=True)
        self.assertEqual(artifacts, ['inspection.json', TITLE + '.ipa', TITLE + '.ipa.sha256',
                                     TITLE + '.romfs.zip', TITLE + '.romfs.zip.sha256'])
        self.assertEqual(len(commands), 1)
        self.assertIn('--separate-romfs', commands[0])

    def test_default_keeps_bundled_romfs(self):
        artifacts, commands = self.run_build(separate=False)
        self.assertEqual(artifacts, ['inspection.json', TITLE + '.ipa', TITLE + '.ipa.sha256'])
        self.assertNotIn('--separate-romfs', commands[0])
        self.assertFalse((self.folder / 'outputs' / (TITLE + '.romfs.zip')).exists())

    def test_both_targets_archive_only_for_ios(self):
        artifacts, commands = self.run_build(targets=('mac', 'ios'), separate=True, system='Darwin')
        self.assertEqual(artifacts, ['inspection.json', TITLE + '.app.zip', TITLE + '.app.zip.sha256',
                                     TITLE + '.ipa', TITLE + '.ipa.sha256',
                                     TITLE + '.romfs.zip', TITLE + '.romfs.zip.sha256'])
        builds = [c for c in commands if c[0] != 'ditto']
        self.assertEqual(len(builds), 2)
        self.assertIn('mac-app', builds[0])
        self.assertNotIn('--separate-romfs', builds[0])
        self.assertIn('--separate-romfs', builds[1])

    def test_language_reaches_the_build_and_no_launch_defaults_are_written(self):
        _, commands = self.run_build(system='Darwin', settings={'DM_LANG': 'ja'})
        build = commands[0]
        self.assertEqual(build[build.index('--language') + 1], 'ja')
        self.assertNotIn('--env-file', build)
        self.assertNotIn('--launch-defaults', build)
        self.assertFalse((self.folder / 'launch-defaults.json').exists())

    def test_windows_gets_the_language_the_same_way(self):
        _, commands = self.run_build(system='Windows', settings={'DM_LANG': 'zh-Hans'})
        build = commands[0]
        self.assertEqual(build[build.index('--language') + 1], 'zh-Hans')
        self.assertNotIn('--env-file', build)
        self.assertNotIn('--launch-defaults', build)

    def test_the_launcher_choice_selects_the_stage(self):
        self.run_build(system='Darwin')
        self.assertEqual(self.ensure.call_args.kwargs['launcher'], 'objc')   # the default
        self.setUp()
        self.run_build(system='Darwin', launcher='swiftui')
        self.assertEqual(self.ensure.call_args.kwargs['launcher'], 'swiftui')
        self.setUp()
        self.run_build(system='Windows', launcher='objc')
        self.assertEqual(self.ensure.call_args.kwargs['launcher'], 'objc')

    def test_swiftui_cannot_be_built_on_windows(self):
        with self.assertRaises(ValueError):
            self.run_build(system='Windows', launcher='swiftui')

    def test_the_launcher_does_not_change_the_build_command(self):
        _, objc = self.run_build(system='Darwin', launcher='objc', settings={'DM_LANG': 'ja'})
        self.setUp()
        _, swift = self.run_build(system='Darwin', launcher='swiftui', settings={'DM_LANG': 'ja'})
        options = lambda command: [x for x in command if x.startswith('--')] + [command[command.index('--language') + 1]]
        self.assertEqual(options(objc[0]), options(swift[0]))   # the same options: only the stage differs

    def test_system_language_adds_no_language_option(self):
        for system in ('Darwin', 'Windows'):
            _, commands = self.run_build(system=system, settings={'DM_LANG': 'system'})
            self.assertNotIn('--language', commands[0])
            self.assertNotIn('--env-file', commands[0])
            self.setUp()

    def test_output_folder_receives_finished_artifacts(self):
        delivered = self.root / 'delivered'
        artifacts, commands = self.run_build(separate=True, output=delivered)
        self.assertIn(TITLE + '.ipa', artifacts)
        for name in (TITLE + '.ipa', TITLE + '.ipa.sha256', TITLE + '.romfs.zip', TITLE + '.romfs.zip.sha256'):
            self.assertTrue((delivered / name).is_file(), name)
        self.assertEqual((delivered / (TITLE + '.ipa')).read_bytes(),
                         (self.folder / 'outputs' / (TITLE + '.ipa')).read_bytes())

    def test_mac_only_separate_is_rejected(self):
        self.task(('mac',), True)
        with patch.dict(os.environ, NX_WEB_CONFIG=json.dumps({'tools': str(self.root / 'tools')})):
            with self.assertRaisesRegex(ValueError, 'iOS target'):
                worker.execute(self.folder)

    def test_selected_missing_sdk_blocks_build(self):
        self.task()
        config = {'tools': str(self.root / 'tools'), 'sdk_version': '27.0'}
        with patch.dict(os.environ, NX_WEB_CONFIG=json.dumps(config)), \
             patch.object(worker.platform, 'system', return_value='Windows'):
            with self.assertRaisesRegex(ValueError, 'SDK version'):
                worker.execute(self.folder)


if __name__ == '__main__':
    unittest.main()
