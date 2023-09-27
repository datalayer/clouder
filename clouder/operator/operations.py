import asyncio
import contextlib
import threading

import kopf
import pykube


operator_ready_flag = threading.Event()
operator_stop_flag = threading.Event()

THREAD = None


class KopfExample(pykube.objects.NamespacedAPIObject):
    version = "kopf.dev/v1"
    endpoint = "kopfexamples"
    kind = "KopfExample"


@kopf.on.create('kopfexamples')
def create_fn(memo: kopf.Memo, **kwargs):
    print(memo.create_tpl.format(**kwargs))


@kopf.on.delete('kopfexamples')
def delete_fn(memo: kopf.Memo, **kwargs):
    print(memo.delete_tpl.format(**kwargs))


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


def create_object(step):
    try:
        api = pykube.HTTPClient(pykube.KubeConfig.from_env())
        kex = KopfExample(api, dict(
            apiVersion='kopf.dev/v1',
            kind='KopfExample',
            metadata=dict(
                namespace='default',
                name=f'kopf-example-{step}',
            ),
        ))
        kex.create()
    except pykube.exceptions.HTTPError as exc:
        if exc.code in [409]:
            pass
        else:
            raise


def delete_object(step):
    try:
        api = pykube.HTTPClient(pykube.KubeConfig.from_env())
        kex = KopfExample.objects(api, namespace='default').get_by_name(f'kopf-example-{step}')
        kex.delete()
    except pykube.exceptions.HTTPError as exc:
        if exc.code in [404]:
            pass
        else:
            raise


def test_operator():
    steps = 3
    for step in range(steps):
        create_object(step)
        delete_object(step)


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
