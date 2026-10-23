# Scene, settings and slicing may only be touched from the Qt main thread.
# Our HTTP server and the agent loop run in worker threads, so every call into
# the slicer goes through run_on_main(), which schedules it with
# Application.callLater() and blocks the worker until it has finished.
import threading
import traceback

from UM.Application import Application


class MainThreadTimeout(Exception):
    pass


def run_on_main(func, *args, **kwargs):
    timeout = kwargs.pop("_timeout", 60)
    if threading.current_thread() is threading.main_thread():
        return func(*args, **kwargs)

    done = threading.Event()
    box = {}

    def wrapper():
        try:
            box["result"] = func(*args, **kwargs)
        except Exception as e:  # re-raised in the worker thread below
            box["error"] = e
            box["traceback"] = traceback.format_exc()
        finally:
            done.set()

    Application.getInstance().callLater(wrapper)
    if not done.wait(timeout):
        raise MainThreadTimeout("Slicer did not respond within %s s" % timeout)
    if "error" in box:
        raise box["error"]
    return box.get("result")
