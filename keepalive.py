"""Keeps the Vanguard Relay server running: start.bat runs this, and this runs relay.py.

- If the server exits unexpectedly it is started again (after --delay seconds). More than --max-restarts
  such exits within --window seconds and it gives up, so a broken server cannot loop forever.
- Exit code 3 from the server means "restart me" (the Restart button): started again after a short pause, never counted.
- Exit code 4 means another server already owns the port: quit quietly.
- data/stop.flag means "do not start it again once it exits". Keepalive never kills the server because of the flag.
- A stop.flag left over from an earlier run is deleted at startup, so an old stop cannot block a fresh start.
Only the child this script started (by its own handle) is ever stopped; nothing is stopped by name.
"""
import argparse, json, os, socket, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXIT_RESTART = 3
EXIT_PORT_IN_USE = 4


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def find_port(data):
    if os.environ.get('RELAY_PORT'):
        return int(os.environ['RELAY_PORT'])
    try:
        return int(json.loads((data / 'config.json').read_text(encoding='utf-8'))['port'])
    except (OSError, ValueError, KeyError, TypeError):
        return 8940


def port_in_use(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


def kill_on_close_job(proc):
    """Windows, best effort: when this process ends, the OS ends the child too. Returns the job handle to keep alive."""
    if os.name != 'nt':
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ('r', 'w', 'o', 'rb', 'wb', 'ob')]

        class Extended(ctypes.Structure):
            _fields_ = [('Basic', Basic), ('Io', IoCounters), ('ProcessMemoryLimit', ctypes.c_size_t),
                        ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t),
                        ('PeakJobMemoryUsed', ctypes.c_size_t)]

        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = Extended(); info.Basic.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None
        k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(proc._handle)))
        return job
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--child', default=str(HERE / 'relay.py'))
    ap.add_argument('--data', default=os.environ.get('RELAY_DATA') or str(HERE / 'data'))
    ap.add_argument('--delay', type=float, default=2)
    ap.add_argument('--max-restarts', type=int, default=5)
    ap.add_argument('--window', type=float, default=600)
    a = ap.parse_args()
    data = Path(a.data); data.mkdir(parents=True, exist_ok=True)
    child = Path(a.child).resolve()
    flag = data / 'stop.flag'
    port = find_port(data)
    if port_in_use(port):
        print(f'Vanguard Relay is already running on port {port}; not starting a second one.', flush=True)
        return 0
    try:
        flag.unlink()
    except OSError:
        pass

    def log(text):
        with (data / 'keepalive.log').open('a', encoding='utf-8') as f:
            f.write(f'{now()} {text}\n')

    crashes, runs, proc, job = [], 0, None, None
    try:
        while True:
            env = os.environ.copy(); env['RELAY_KEEPALIVE'] = '1'
            if runs:
                env['RELAY_WAIT_PORT'] = '1'
            runs += 1
            with (data / 'server.log').open('ab') as out, (data / 'server.err.log').open('ab') as err:
                proc = subprocess.Popen([sys.executable, '-B', str(child)], cwd=str(child.parent), env=env,
                                        stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if job is None:
                job = kill_on_close_job(proc)
            while True:   # just wait: stop.flag only decides what happens after the exit
                try:
                    proc.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    pass
            code = proc.returncode
            if flag.exists():
                try:
                    flag.unlink()
                except OSError:
                    pass
                log(f'exit code {code}, stop.flag found, not restarting')
                return 0
            if code == EXIT_PORT_IN_USE:
                log(f'exit code {code}, port in use, not restarting')
                return 0
            if code == EXIT_RESTART:
                log(f'exit code {code}, restarting')
                time.sleep(0.5)
                continue
            t = time.monotonic()
            crashes = [x for x in crashes if t - x < a.window]
            if len(crashes) >= a.max_restarts:
                log(f'exit code {code}, giving up')
                with (data / 'server.err.log').open('a', encoding='utf-8') as f:
                    f.write(f'{now()} keepalive: the server stopped {a.max_restarts} times in {int(a.window // 60)} minutes; '
                            'not restarting it again. Run start.bat to try again.\n')
                return 1
            crashes.append(t)
            log(f'exit code {code}, restarting')
            time.sleep(a.delay)
    except KeyboardInterrupt:
        return 0
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == '__main__':
    sys.exit(main())
