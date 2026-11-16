"""Ejecutor local del orquestador (sin Durable Functions): sirve para pruebas y para `scripts/simulate_local.py`.

Simula el contexto Durable: las actividades corren de inmediato, los timers avanzan el reloj
y los eventos externos se toman de un guion (`events`)."""
from datetime import timedelta

from . import clock, lifecycle


class _Task:
    def __init__(self, kind, **kw):
        self.kind, self.result, self.cancelled = kind, None, False
        self.__dict__.update(kw)

    def cancel(self):
        self.cancelled = True


class FakeContext:
    def __init__(self, inp, events=None):
        self._inp, self.events = inp, list(events or [])
        self.log = []

    @property
    def current_utc_datetime(self):
        return clock.now()

    def get_input(self):
        return self._inp

    def call_activity(self, name, inp=None):
        return _Task("act", name=name, inp=inp)

    def call_activity_with_retry(self, name, retry, inp=None):
        return _Task("act", name=name, inp=inp)

    def task_all(self, tasks):
        return _Task("all", tasks=tasks)

    def task_any(self, tasks):
        return _Task("any", tasks=tasks)

    def create_timer(self, fire_at):
        return _Task("timer", fire_at=fire_at)

    def wait_for_external_event(self, name):
        return _Task("event", name=name)


def run_lifecycle(incident, events=None, on_step=None, max_steps=2000):
    """Ejecuta `lifecycle.run` completo. `events` = decisiones del operador en orden (p. ej. ['CONFIRMAR'])."""
    ctx = FakeContext(lifecycle.start_input(incident), events)
    gen = lifecycle.run(ctx, retry=None)

    def resolve(task):
        if task.kind == "act":
            r = lifecycle.ACTIVITIES[task.name](task.inp)
            ctx.log.append(task.name)
            if on_step:
                on_step(task.name, task.inp, r)
            return r
        if task.kind == "all":
            return [resolve(t) for t in task.tasks]
        if task.kind == "timer":
            wait = (task.fire_at - clock.now()).total_seconds()
            clock.advance(seconds=max(wait, 0))
            return None
        if task.kind == "event":
            if not ctx.events:
                raise RuntimeError("el orquestador espera un evento externo y el guion no tiene más")
            task.result = ctx.events.pop(0)
            return task.result
        if task.kind == "any":
            ev = next((t for t in task.tasks if t.kind == "event"), None)
            if ev is not None and ctx.events:
                resolve(ev)
                return ev
            tm = next(t for t in task.tasks if t.kind == "timer")
            resolve(tm)
            return tm
        raise ValueError(task.kind)

    try:
        task, steps = next(gen), 0
        while True:
            steps += 1
            if steps > max_steps:
                raise RuntimeError("demasiados pasos: posible bucle infinito")
            res = resolve(task)
            task = gen.send(res)
    except StopIteration as e:
        return e.value, ctx.log
