import sys
import time
import unittest
from unittest.mock import patch
from zkdictate import text_processors
from zkdictate.text_processors import ProcessorError, process, run_command


def command(code, *args):
    return {'name': 'external_command', 'path': sys.executable, 'args': ['-c', code, *args]}


class TextProcessorTests(unittest.TestCase):
    def test_missing_or_empty_steps_leave_text_alone(self):
        self.assertEqual(process('Hello.', None), ('Hello.', []))
        self.assertEqual(process('Hello.', []), ('Hello.', []))
        self.assertEqual(process('', [command('raise SystemExit(1)')]), ('', []))

    def test_steps_run_in_registry_order_and_failures_fall_back(self):
        calls = []
        def step(label, fail=False):
            def factory(_):
                def run(text):
                    calls.append(label)
                    if fail: raise RuntimeError(text)
                    return f'{text}+{label}'
                return run
            return (label.title(), factory)
        registry = {'a': step('a'), 'b': step('b', fail=True), 'c': step('c')}
        with patch.dict(text_processors.PROCESSORS, registry, clear=True):
            text, warnings = process('secret words', [{'name': 'c'}, {'name': 'b'}, {'name': 'a'}, {'name': 'zzz'}])
        self.assertEqual(calls, ['a', 'b', 'c'])
        self.assertEqual(text, 'secret words+a+c')
        self.assertEqual(warnings, ['Unknown text processor skipped.', 'B skipped: failed.'])
        self.assertNotIn('secret', ' '.join(warnings))

    def test_invalid_settings_keep_text(self):
        self.assertEqual(process('Hi', 'external_command'), ('Hi', ['Text processing skipped: invalid settings.']))
        for config in ({}, {'path': ''}, {'path': 'relative/tool'}, {'path': '/bin/cat', 'args': 'x'}, {'path': '/bin/cat', 'args': [1]}):
            text, warnings = process('Hi', [{'name': 'external_command', **config}])
            self.assertEqual(text, 'Hi')
            self.assertEqual(len(warnings), 1)
            self.assertTrue(warnings[0].startswith('External command skipped:'))

    def test_external_command_success_uses_stdin_args_and_strips_output(self):
        code = 'import sys; print(sys.stdin.read().upper() + sys.argv[1])'
        self.assertEqual(process('héllo', [command(code, '!')]), ('HÉLLO!', []))

    def test_external_command_failures_keep_previous_text(self):
        cases = {
            'exited with status 3': 'import sys; sys.stdout.write("partial"); sys.exit(3)',
            'returned no text': 'import sys; sys.stdin.read()',
            'output is not UTF-8': 'import sys; sys.stdout.buffer.write(b"\\xff")',
        }
        for reason, code in cases.items():
            with self.subTest(reason):
                self.assertEqual(process('keep me', [command(code)]), ('keep me', [f'External command skipped: {reason}.']))
        missing = {'name': 'external_command', 'path': '/nonexistent/zkdictate-tool'}
        self.assertEqual(process('keep me', [missing]), ('keep me', ['External command skipped: could not start.']))

    def test_timeout_kills_command(self):
        started = time.monotonic()
        with self.assertRaisesRegex(ProcessorError, 'timed out'):
            run_command([sys.executable, '-c', 'import time; time.sleep(30)'], 'x', timeout=0.5)
        self.assertLess(time.monotonic() - started, 5)
        with patch.object(text_processors, 'TIMEOUT', 0.5):
            self.assertEqual(process('keep', [command('import time; time.sleep(30)')]), ('keep', ['External command skipped: timed out.']))

    def test_oversized_output_is_refused(self):
        with self.assertRaisesRegex(ProcessorError, 'output too large'):
            run_command([sys.executable, '-c', 'import sys; sys.stdout.write("x" * 5000)'], '', limit=4096)
        flood = 'import sys\nwhile True: sys.stdout.write("x" * 65536)'
        self.assertEqual(process('keep', [command(flood)]), ('keep', ['External command skipped: output too large.']))

    def test_command_ignoring_stdin_with_large_input(self):
        self.assertEqual(run_command([sys.executable, '-c', 'print("ok")'], 'x' * 1_000_000), 'ok\n')


if __name__ == '__main__':
    unittest.main()
