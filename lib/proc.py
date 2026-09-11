"""
Small subprocess wrapper used everywhere this app shells out to ffmpeg /
fluidsynth. On its own, a missing program on Windows shows up as a bare
`FileNotFoundError: [WinError 2] The system cannot find the file specified`
with no indication of *which* program was missing -- this turns that (and a
failing command's stderr) into a message that actually says what to do.
"""
import subprocess


def run(cmd, **kwargs):
    kwargs.setdefault("check", True)
    kwargs.setdefault("capture_output", True)
    program = cmd[0]
    try:
        return subprocess.run(cmd, **kwargs)
    except FileNotFoundError:
        raise RuntimeError(
            f"Could not find '{program}' on your PATH. Make sure it's "
            "installed, then close this window COMPLETELY and double-click "
            "run.bat again (a brand new window is needed to see a "
            "just-installed program -- reopening the same window isn't "
            "enough)."
        ) from None
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"'{program}' failed:\n{stderr[-2000:]}") from None
