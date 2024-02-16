import asyncio
import logging
import contextlib
import threading

import kopf

from .handlers import ssh_key


# registry = kopf.OperatorRegistry()
# kopf.set_default_registry(registry)

operator_ready_flag = threading.Event()
operator_stop_flag = threading.Event()

THREAD = None

def is_it_not_a_timer(record: logging.LogRecord) -> bool:
    txt = record.getMessage()
    return not txt.startswith("Timer ")

@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, logger, **_):
#    kopf.configure(quiet=True)
#    settings.posting.level = logging.ERROR
    objlogger = logging.getLogger('kopf.objects')
    objlogger.addFilter(is_it_not_a_timer)
    logger.info('Clouder operator starting.')


def kopf_thread(ready_flag: threading.Event, stop_flag: threading.Event):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    with contextlib.closing(loop):
        kopf.configure(verbose=False)
        loop.run_until_complete(kopf.operator(
            ready_flag=ready_flag,
            stop_flag=stop_flag,
            memo=kopf.Memo(
                create_tpl="Create tpl {name}.",
                delete_tpl="Delete tpl {name}.",
            ),
        ))


def start_operator():
    print("Starting the Clouder operator.")
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
    print("Stopping the Clouder operator.")
    operator_stop_flag.set()
    THREAD.join()
