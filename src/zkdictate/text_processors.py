"""Optional transcript rewriting. A failed step keeps the text from before it."""
from __future__ import annotations
import os
import select
import signal
import subprocess
import threading
import time

TIMEOUT = 5
MAX_OUTPUT = 1024 * 1024


class ProcessorError(Exception):
    """A short, content-free reason shown to the user."""


def _feed(pipe, data):
    try: pipe.write(data)
    except (BrokenPipeError, OSError): pass
    finally:
        try: pipe.close()
        except OSError: pass


def run_command(argv, text, timeout=TIMEOUT, limit=MAX_OUTPUT):
    """Run argv without a shell: text on stdin, bounded stdout back, stderr discarded."""
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        raise ProcessorError('could not start') from None
    try:
        threading.Thread(target=_feed, args=(process.stdin, text.encode()), daemon=True).start()
        deadline = time.monotonic() + timeout
        output = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise ProcessorError('timed out')
            if not select.select([process.stdout], [], [], remaining)[0]: continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk: break
            output += chunk
            if len(output) > limit: raise ProcessorError('output too large')
        try: code = process.wait(max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired: raise ProcessorError('timed out') from None
        if code: raise ProcessorError(f'exited with status {code}')
        try: return output.decode()
        except UnicodeDecodeError: raise ProcessorError('output is not UTF-8') from None
    finally:
        if process.poll() is None:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait()
        process.stdout.close()


def external_command(config):
    path, args = config.get('path'), config.get('args', [])
    if not isinstance(path, str) or not path:
        raise ProcessorError('no command chosen')
    if not os.path.isabs(path) or not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ProcessorError('invalid command settings')
    def run(text):
        result = run_command([path, *args], text).strip()
        # An empty result would silently discard the dictation.
        if not result: raise ProcessorError('returned no text')
        return result
    return run


# Factories receive the request's settings for that step and return fn(text) -> str.
# Steps always run in this order, whatever order the request lists them in.
PROCESSORS = {
    'external_command': ('External command', external_command),
}


def process(text, steps):
    """Apply enabled steps; return the text and short warnings for skipped steps."""
    if not text or steps is None: return text, []
    if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
        return text, ['Text processing skipped: invalid settings.']
    configs = {s.get('name'): s for s in steps}
    warnings = ['Unknown text processor skipped.' for name in configs if name not in PROCESSORS]
    for name, (title, factory) in PROCESSORS.items():
        if name not in configs: continue
        try:
            result = factory(configs[name])(text)
            if not isinstance(result, str): raise ProcessorError('returned no text')
            text = result
        except ProcessorError as exc:
            warnings.append(f'{title} skipped: {exc}.')
        except Exception:
            # Exception text could quote the transcript; report only the step.
            warnings.append(f'{title} skipped: failed.')
    return text, warnings
