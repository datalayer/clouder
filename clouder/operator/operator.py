import asyncio
import contextlib
import threading

import kopf

from .handlers import sshkey


# registry = kopf.OperatorRegistry()
# kopf.set_default_registry(registry)

operator_ready_flag = threading.Event()
operator_stop_flag = threading.Event()

THREAD = None


@kopf.on.startup()
def sshkey_startup(**_):
    print('Clouder operator starting with handlers', sshkey)


def kopf_thread(ready_flag: threading.Event, stop_flag: threading.Event):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    with contextlib.closing(loop):
        kopf.configure(verbose=False)
        loop.run_until_complete(kopf.operator(
            ready_flag=ready_flag,
            stop_flag=stop_flag,
            memo=kopf.Memo(
                create_tpl="Hello, {name}!",
                delete_tpl="Good bye, {name}!",
            ),
        ))


def start_operator():
    print("Starting the operator.")
    global THREAD
    THREAD = threading.Thread(
        target = kopf_thread,
        kwargs = dict(
            stop_flag = operator_stop_flag,
            ready_flag = operator_ready_flag,
        )
    )
    THREAD.start()
    operator_ready_flag.wait()


def stop_operator():
    print("Stopping the operator.")
    operator_stop_flag.set()
    THREAD.join()
