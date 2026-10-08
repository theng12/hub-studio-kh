"""Physical scheduler regression coverage, including ranked FIFO superseding scarcity.

Owner decisions, 2026-09-30 (audit-20260930/DESIGN-scheduler.md):

* A model's RAM requirement is a **hard floor**, per model - measured peak, else the manifest's
  qualification, else the catalog. A Mac below it is not a candidate for that model at all.
* A freed Mac takes the highest ranked eligible function, then the oldest queued batch.
  With no preference every eligible function shares a rank and queue age decides.
* Never idle while a Mac has eligible queued work; nothing is preempted, nothing is reserved.
* A job pinned to one Mac ("Send here") has a pool of one, so it wins on that Mac.
* Chat/LLM and transcription are in the same contest as image, voice and music.

The centrepiece is a simulation of the owner's fleet - 10 x 8 GB, 5 x 16, 2 x 24, 1 x 32 - running
the real dispatch loops (`broker._dispatch_loop`, `chat_jobs.dispatch_once`) on a fake clock, with the
worker calls replaced by timers. Three kinds of work arrive at different times:

    image  (8 GB floor,  60 s each)  500 jobs at t=0
    voice  (16 GB floor, 30 s each)  200 jobs at t=600
    llm    (24 GB floor, 20 s each)  100 jobs at t=1200

The historical scarcity metric remains in the simulation as evidence that FIFO deliberately
supersedes it. Lease, admission, RAM-floor, work-conservation, and retry probes remain unchanged.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import time as real_time

import httpx
import pytest

from components.hub.backend import broker, chat_jobs, ledger, memory_admission, transcription_jobs

# An "8 GB" Mac reports 8 GiB, which is 8.59 decimal GB; the tier names are what the owner says.
GIB = 1.0737

#: kind -> (modality, model id, RAM requirement GB, seconds per job)
KINDS = {
    "image": ("image", "sim/image-8gb", 8, 60.0),
    "voice": ("voice", "sim/voice-16gb", 16, 30.0),
    "llm": ("chat", "sim/llm-24gb", 24, 20.0),
}
FLEET = ((8, 10), (16, 5), (24, 2), (32, 1))


class _Clock:
    """The one clock the schedulers under test read."""

    def __init__(self) -> None:
        self.now = 1_000_000.0

    def time(self) -> float:
        return self.now

    def __getattr__(self, name):  # monotonic, sleep, ... stay real
        return getattr(real_time, name)


class _Refusal:
    def __init__(self, status: int, payload: dict):
        self.status, self.payload = status, payload

    async def post(self, url, **kwargs):
        return httpx.Response(self.status, json=self.payload,
                              request=httpx.Request("POST", url))


class _Monitor:
    """Just the parts of `StudioMonitor` the dispatchers read, over a scripted fleet."""

    def __init__(self, studios, rows):
        self.registry = studios
        self.status = {studio["id"]: {"status": "up"} for studio in studios}
        self.rows = rows            # modality -> [catalog row every Mac of that modality publishes]
        self._catalog_cache = {}
        self._transcribe_cache = {}
        self._client = None

    def cached_catalog_entries(self, model, *, modality=None):
        return [{"studio": studio, "entry": row} for studio in self.registry
                for row in self.rows.get(studio["modality"], [])
                if row["repo"] == model and modality in (None, studio["modality"])]

    def scheduling_catalog_entries(self, model, *, modality=None):
        return [m for m in self.cached_catalog_entries(model, modality=modality)
                if self.status[m["studio"]["id"]]["status"] == "up"]

    async def scheduling_catalog(self, studio):
        if self.status[studio["id"]]["status"] != "up":
            return None
        return {"models": list(self.rows.get(studio["modality"], []))}

    async def scheduling_transcription(self, studio):
        return None


class Sim:
    """A fake-clock harness around the real broker and Chat loops."""

    def __init__(self, monkeypatch, fleet=FLEET, kinds=None):
        self.clock = _Clock()
        self.kinds = dict(kinds or KINDS)
        self.macs: dict[str, int] = {}            # machine -> nominal GB
        self.online: set[str] = set()
        studios = []
        for gb, count in fleet:
            for number in range(count):
                machine = f"m{gb}-{number:02d}"
                self.macs[machine] = gb
                self.online.add(machine)
                for modality in sorted({spec[0] for spec in self.kinds.values()}):
                    studios.append({
                        "id": f"{modality}@{machine}", "modality": modality, "machine": machine,
                        "host": "127.0.0.1", "port": 1, "local": False,
                        "runtime_identity": {"id": "runtime", "compatible": True},
                    })
        rows: dict[str, list] = {}
        for spec in self.kinds.values():
            rows.setdefault(spec[0], []).append({
                "repo": spec[1], "cache": {"state": "cached"}, "min_unified_memory_gb": spec[2]})
        self.monitor = _Monitor(studios, rows)
        self.heap: list = []
        self.counter = itertools.count()
        self.log: list[dict] = []                 # every job that started
        self.running: dict[str, dict] = {}        # machine -> running job
        self.overlaps: list[tuple] = []
        self.idle_violations: list[tuple] = []
        self.order_violations: list[tuple] = []
        self.passes = 0
        self.refusals = 0
        self.chat_first = False
        self.paused: set[str] = set()             # kinds no Mac may run (paused everywhere)
        self.refuse_until: dict[str, float] = {}  # machine -> t: a peer that answers MACHINE_BUSY
        self.kind_of_model = {spec[1]: kind for kind, spec in self.kinds.items()}

        m = monkeypatch
        m.setattr(broker, "time", self.clock)
        m.setattr(chat_jobs, "time", self.clock)
        broker.configure_monitor(self.monitor)
        m.setattr(broker, "_host_for_studio", self._host)
        m.setattr(broker.peers, "dispatch_ready", lambda studio, generation=None: True)
        m.setattr(broker.peers, "dispatch_generation", lambda studio: None)
        m.setattr(broker, "machine_enabled", lambda machine: True)
        m.setattr(broker, "studio_enabled", lambda machine, studio_id: True)
        m.setattr(chat_jobs, "machine_enabled", lambda machine: True)
        m.setattr(chat_jobs, "studio_enabled", lambda machine, studio_id: True)
        m.setattr(ledger, "save_batch", lambda batch: None)
        m.setattr(chat_jobs, "_save", lambda batch: None)
        m.setattr(chat_jobs, "_wakeup", asyncio.Event())
        m.setattr(transcription_jobs, "_wakeup", asyncio.Event())
        self._real_run_item = broker._run_item
        m.setattr(broker, "_run_item", self._run_item)
        m.setattr(chat_jobs, "_run_pack", self._run_pack)
        real_queue = broker._queued_batches
        real_chat = chat_jobs.dispatch_once

        def counted_queue():
            self.passes += 1
            return real_queue()

        async def counted_chat(monitor):
            self.passes += 1
            return await real_chat(monitor)

        m.setattr(broker, "_queued_batches", counted_queue)
        m.setattr(chat_jobs, "dispatch_once", counted_chat)

    # ── what the workers would say ────────────────────────────────────────────────
    def _host(self, studio):
        return {"total_gb": round(self.macs[studio["machine"]] * GIB, 2), "available_gb": 6.0}

    def _start(self, machine: str, kind: str) -> dict:
        if machine in self.running:
            self.overlaps.append((self.clock.now, machine, self.running[machine]["kind"], kind))
        job = {"t": self.clock.now, "machine": machine, "gb": self.macs[machine], "kind": kind}
        self.running[machine] = job
        self.log.append(job)
        return job

    def _finish(self, machine: str) -> None:
        job = self.running.pop(machine, None)
        if job is not None:
            job["end"] = self.clock.now

    def _timer(self, seconds: float):
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self.heap, (self.clock.now + seconds, next(self.counter), future))
        return future

    async def _run_item(self, client, batch, item, studio):
        machine = studio["machine"]
        kind = self.kind_of_model[batch["model"]]
        if self.refuse_until.get(machine, 0) > self.clock.now:
            # A joined node whose own work holds its Mac: the peer answers 409 MACHINE_BUSY,
            # and the real refusal handling in `_run_item` decides what happens to the item.
            self.refusals += 1
            refuser = _Refusal(409, {"detail": "local is reserved for other work on this Mac",
                                     "code": "MACHINE_BUSY"})
            await self._real_run_item(refuser, batch, item, studio)
            return
        self._start(machine, kind)
        await self._timer(self.kinds[kind][3])
        item["state"] = "done"
        self._finish(machine)
        broker._busy.discard(studio["id"])
        broker._wakeup.set()

    async def _run_pack(self, monitor, batch, pack, studio, owner, generation=None):
        machine = studio["machine"]
        kind = self.kind_of_model[batch["model"]]
        self._start(machine, kind)
        await self._timer(self.kinds[kind][3])
        pack["state"] = "done"
        self._finish(machine)
        chat_jobs.busy_studios.discard(studio["id"])
        chat_jobs._pack_tasks.pop((batch["id"], pack["index"]), None)
        if self.chat_first:
            chat_jobs._wakeup.set()
        broker.release_external_machine(machine, owner)     # wakes the broker loop
        chat_jobs._wakeup.set()

    # ── submitting work ───────────────────────────────────────────────────────────
    def submit(self, kind: str, count: int, routing: str = "pool") -> str:
        modality, model, _floor, _seconds = self.kinds[kind]
        if modality == "chat":
            now = self.clock.now
            batch = {
                "id": f"chat{next(self.counter)}", "created_at": now, "updated_at": now,
                "finished_at": None, "last_dispatched_at": 0, "cancelled": False,
                "model": model, "kind": "completion", "label": None, "project": None,
                "episode": None, "genstudio_execution": None,
                "packs": [{"index": i, "pack_id": f"p{i}", "scene_ids": [f"s{i}"],
                           "messages": [], "params": {}, "state": "queued", "tries": 0,
                           "studio": None, "retry_at": None, "results": {}}
                          for i in range(count)],
            }
            chat_jobs.batches[batch["id"]] = batch
            chat_jobs._wakeup.set()
        else:
            out = broker.submit_batch({
                "modality": modality, "model": model, "routing": routing,
                "items": [{"prompt": str(i)} for i in range(count)],
            })
            assert "error" not in out, out
            batch = broker.batches[out["batch_id"]]
        broker._wakeup.set()
        return batch["id"]

    def queued(self, kind: str) -> int:
        modality, model = self.kinds[kind][:2]
        if modality == "chat":
            return sum(pack["state"] == "queued" for batch in chat_jobs.batches.values()
                       if batch["model"] == model for pack in batch["packs"])
        return sum(item["state"] == "queued" for batch in broker.batches.values()
                   if batch["model"] == model for item in batch["items"])

    def eligible_kinds(self, machine: str) -> list[str]:
        """The policy's own answer to "what could this Mac run": RAM floor and queue only."""
        return [kind for kind, spec in self.kinds.items()
                if self.macs[machine] >= spec[2] and self.queued(kind) > 0
                and kind not in self.paused]

    def pool(self, kind: str) -> int:
        return sum(1 for machine in self.online if self.macs[machine] >= self.kinds[kind][2])

    # ── the oracle ────────────────────────────────────────────────────────────────
    def check(self) -> None:
        """After a scheduling pass has settled: is every Mac doing what the policy says?"""
        for machine in sorted(self.online):
            eligible = self.eligible_kinds(machine)
            if machine not in self.running and eligible:
                self.idle_violations.append((self.clock.now, machine, eligible))
            job = self.running.get(machine)
            if job is not None and job["t"] == self.clock.now:
                scarcer = [k for k in eligible if self.pool(k) < self.pool(job["kind"])]
                if scarcer:
                    self.order_violations.append((self.clock.now, machine, job["kind"], scarcer))

    async def settle(self) -> None:
        """Let both loops run until neither has anything more to do."""
        for wake in ((chat_jobs._wakeup, broker._wakeup) if self.chat_first
                     else (broker._wakeup, chat_jobs._wakeup)):
            wake.set()
        quiet, seen = 0, -1
        for _ in range(4000):
            await asyncio.sleep(0)
            if self.passes == seen:
                quiet += 1
                if quiet >= 6:
                    return
            else:
                quiet, seen = 0, self.passes
        raise AssertionError("the dispatch loops never went quiet")

    def set_online(self, machine: str, up: bool) -> None:
        (self.online.add if up else self.online.discard)(machine)
        for studio in self.monitor.registry:
            if studio["machine"] == machine:
                self.monitor.status[studio["id"]]["status"] = "up" if up else "down"

    def busy_seconds(self, until: float) -> dict[int, float]:
        """Seconds each RAM tier spent working, up to ``until``."""
        totals = {gb: 0.0 for gb, _count in FLEET}
        until += self.start
        for job in self.log:
            end = min(job.get("end", until), until)
            totals[job["gb"]] += max(0.0, end - job["t"])
        return totals

    def arrive(self, pending: list) -> None:
        for row in [row for row in pending if row[0] <= self.clock.now]:
            pending.remove(row)
            row[2]()

    # ── the driver ────────────────────────────────────────────────────────────────
    async def run(self, arrivals, *, horizon: float = 20_000.0, until_empty: bool = True,
                  chat_first: bool = False):
        """``arrivals``: [(seconds after start, callable)]. Returns the makespan."""
        start = self.start = self.clock.now
        broker._wakeup = asyncio.Event()      # each run has its own loop; events are loop-bound
        chat_jobs._wakeup = asyncio.Event()
        transcription_jobs._wakeup = asyncio.Event()
        pending = sorted(((start + t, next(self.counter), fn) for t, fn in arrivals),
                         key=lambda row: (row[0], row[1]))
        # Which loop wins a same-instant race is an accident of scheduling, so tests run both orders.
        self.chat_first = chat_first
        loops = [broker._dispatch_loop(), chat_jobs._dispatch_loop(self.monitor)]
        tasks = [asyncio.create_task(loop) for loop in (loops[::-1] if chat_first else loops)]
        try:
            while True:
                self.arrive(pending)
                await self.settle()
                self.check()
                if until_empty and not pending and not self.heap and not any(
                        self.queued(kind) for kind in self.kinds):
                    return self.clock.now - start
                assert self.clock.now - start <= horizon, (
                    "the simulation did not drain: work is stranded")
                candidates = ([self.heap[0][0]] if self.heap else []) + (
                    [pending[0][0]] if pending else [])
                if not candidates:
                    return self.clock.now - start     # nothing runs and nothing arrives
                # The real loops re-run on a heartbeat (broker 3 s) even when nothing wakes them.
                candidates.append(self.clock.now + 3.0)
                self.clock.now = max(self.clock.now, min(candidates))
                self.arrive(pending)      # work that arrives the instant a Mac frees is already queued
                while self.heap and self.heap[0][0] <= self.clock.now:
                    heapq.heappop(self.heap)[2].set_result(None)
                for _ in range(20):
                    await asyncio.sleep(0)           # let completions post their state
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture
def sim(reset, monkeypatch):
    return Sim(monkeypatch)


def _report(sim: Sim, makespan: float) -> str:
    busy = sim.busy_seconds(makespan)
    lines = [f"makespan {makespan:.0f}s, {len(sim.log)} jobs"]
    for gb, count in FLEET:
        lines.append(f"  {gb:>2} GB x{count:<2} utilisation {busy[gb] / (count * makespan):.1%}")
    lines.append(f"  idle-while-eligible: {len(sim.idle_violations)}, "
                 f"scarcest-first violations: {len(sim.order_violations)}")
    return "\n".join(lines)


# ─────────────────────────────── the owner's scenario ───────────────────────────────

@pytest.mark.parametrize("chat_first", [False, True])
def test_owner_fleet_mix_and_arrivals(sim, capsys, chat_first):
    """500 images at t=0, 200 voice at t=600, 100 LLM at t=1200, on 10x8, 5x16, 2x24, 1x32 GB."""
    arrivals = [(0, lambda: sim.submit("image", 500)),
                (600, lambda: sim.submit("voice", 200)),
                (1200, lambda: sim.submit("llm", 100))]
    makespan = asyncio.run(sim.run(arrivals, chat_first=chat_first))
    with capsys.disabled():
        print("\n" + _report(sim, makespan))

    kinds = [job["kind"] for job in sim.log]
    assert (kinds.count("image"), kinds.count("voice"), kinds.count("llm")) == (500, 200, 100), (
        "every job must finish: nothing is dropped and nothing starves")
    assert not sim.overlaps, f"two jobs on one Mac at once (a lease was ignored): {sim.overlaps[:3]}"
    assert not sim.idle_violations, (
        f"never idle while eligible work is queued - a Mac sat idle beside work it could run: "
        f"{sim.idle_violations[:3]}")
    starts = {kind: [job["t"] for job in sim.log if job["kind"] == kind]
              for kind in ("image", "voice", "llm")}
    assert max(starts["image"]) <= min(starts["voice"])
    assert max(starts["voice"]) <= min(starts["llm"]), (
        "no-preference mode must drain older eligible batches before newer functions")
    small = [job for job in sim.log if job["gb"] == 8 and job["kind"] != "image"]
    assert not small, f"the RAM floor is a wall: an 8 GB Mac was given {small[0]['kind']} work"
    mid = [job for job in sim.log if job["gb"] == 16 and job["kind"] == "llm"]
    assert not mid, "a 16 GB Mac was given work that needs 24 GB"
    # Non-preemption: every job ran for exactly its own duration.
    for job in sim.log:
        assert job["end"] - job["t"] == pytest.approx(KINDS[job["kind"]][3])


def test_a_newer_scarcer_job_waits_behind_older_fifo_work_without_preference(sim):
    """A newer Voice batch does not bypass older eligible Image work in equal-rank mode."""
    arrivals = [(0, lambda: sim.submit("image", 500)), (600, lambda: sim.submit("voice", 200))]
    asyncio.run(sim.run(arrivals))
    start = min(job["t"] for job in sim.log)
    voice_first = min(job["t"] for job in sim.log if job["kind"] == "voice")
    assert voice_first - start > 600 + 60, "legacy scarcity still bypassed the older FIFO batch"
    assert max(job["t"] for job in sim.log if job["kind"] == "image") <= voice_first


# ─────────────────────────────── ties: loaded model, then FIFO ───────────────────────────────

def test_equal_rank_uses_fifo_before_loaded_model_stickiness(reset, monkeypatch):
    kinds = {"a": ("image", "sim/image-a", 8, 60.0), "b": ("image", "sim/image-b", 8, 60.0)}
    sim = Sim(monkeypatch, fleet=((8, 4),), kinds=kinds)

    # Round one: nothing is loaded anywhere, so two equal models share the Macs first come first
    # served - two Macs on each, because each batch has two items.
    asyncio.run(sim.run([(0, lambda: sim.submit("a", 2)), (0, lambda: sim.submit("b", 2))]))
    loaded = {job["machine"]: job["kind"] for job in sim.log}
    assert sorted(loaded.values()) == ["a", "a", "b", "b"]

    # Round two: B is queued first. Stable FIFO is authoritative even when two
    # Macs still hold A, so every first free slot takes B.
    sim.log.clear()
    sim.clock.now += 100
    def b_then_a():
        sim.submit("b", 4)
        sim.clock.now += 0.5     # B is older by half a second
        sim.submit("a", 4)

    asyncio.run(sim.run([(0, b_then_a)]))
    first_round = [job for job in sim.log if job["t"] == min(j["t"] for j in sim.log)]
    assert len(first_round) == 4
    assert [job["kind"] for job in first_round] == ["b"] * 4


def test_equal_pools_with_nothing_loaded_are_first_come_first_served(reset, monkeypatch):
    """Voice queued before image, and both can use only the 16 GB+ Macs: voice goes first."""
    sim = Sim(monkeypatch, fleet=((16, 2),))
    asyncio.run(sim.run([(0, lambda: sim.submit("voice", 4)), (1, lambda: sim.submit("image", 4))]))
    order = [job["kind"] for job in sorted(sim.log, key=lambda job: job["t"])]
    assert order[:4] == ["voice"] * 4, f"an older equal-pool job was passed over: {order}"


# ─────────────────────────────── starvation and pins ───────────────────────────────

def test_with_only_big_macs_online_nothing_starves(sim):
    for machine in [m for m, gb in sim.macs.items() if gb == 8]:
        sim.set_online(machine, False)
    arrivals = [(0, lambda: sim.submit("image", 60)), (0, lambda: sim.submit("voice", 60)),
                (0, lambda: sim.submit("llm", 30))]
    asyncio.run(sim.run(arrivals))
    kinds = [job["kind"] for job in sim.log]
    assert (kinds.count("image"), kinds.count("voice"), kinds.count("llm")) == (60, 60, 30)
    assert not sim.idle_violations
    assert not [job for job in sim.log if job["gb"] < 16]


def test_llm_waits_when_no_24gb_mac_is_online_and_nobody_idles(sim):
    for machine in [m for m, gb in sim.macs.items() if gb >= 24]:
        sim.set_online(machine, False)
    arrivals = [(0, lambda: sim.submit("llm", 10)), (0, lambda: sim.submit("image", 90))]
    asyncio.run(sim.run(arrivals, until_empty=False, horizon=3000))
    assert sim.queued("llm") == 10, "no online Mac can run the LLM: it must wait, not fail or run"
    assert not [job for job in sim.log if job["kind"] == "llm"]
    assert sim.queued("image") == 0 and len([j for j in sim.log if j["kind"] == "image"]) == 90
    assert not sim.idle_violations, "the LLM's wait must not hold any Mac idle"


def test_a_newer_pinned_job_stays_on_its_mac_but_does_not_bypass_fifo(sim):
    """A pin constrains placement; it does not become an implicit function priority."""
    target = "m16-00"
    # Plenty of voice work first in line, so without the pin this Mac would take voice.
    arrivals = [(0, lambda: sim.submit("voice", 40)),
                (1, lambda: sim.submit("image", 3, routing=f"machine:{target}"))]
    asyncio.run(sim.run(arrivals))
    on_target = [job["kind"] for job in sorted(sim.log, key=lambda job: job["t"])
                 if job["machine"] == target]
    first_image = on_target.index("image")
    assert on_target[:first_image] and set(on_target[:first_image]) == {"voice"}
    assert [job["machine"] for job in sim.log if job["kind"] == "image"] == [target] * 3


def test_a_model_paused_everywhere_has_no_pool_and_blocks_nothing(sim):
    """Pool 0 sorts last and holds nothing: the paused model's older batch does not stall others."""
    paused_model = KINDS["voice"][1]
    for studio in sim.monitor.registry:
        if studio["modality"] == "voice":
            broker._model_protection[(studio["id"], paused_model)] = {
                "blocked": True, "reason": "paused for the test"}
    assert broker.pool_machines("voice", paused_model, "pool") == set()
    sim.paused.add("voice")
    arrivals = [(0, lambda: sim.submit("voice", 5)), (1, lambda: sim.submit("image", 40))]
    asyncio.run(sim.run(arrivals, until_empty=False, horizon=2000))
    assert sim.queued("voice") == 5
    assert sim.queued("image") == 0
    assert not sim.idle_violations


# ─────────────────────────────── the pool itself ───────────────────────────────

def test_pool_counts_distinct_macs_that_meet_the_floor_and_busy_ones_count(sim):
    assert len(broker.pool_machines("image", KINDS["image"][1], "pool")) == 18
    assert len(broker.pool_machines("voice", KINDS["voice"][1], "pool")) == 8
    assert len(broker.pool_machines("chat", KINDS["llm"][1], "pool")) == 3
    # Busy is capacity, not availability.
    broker._busy.add("voice@m32-00")
    assert len(broker.pool_machines("voice", KINDS["voice"][1], "pool")) == 8
    # A pin is a pool of one, and a pin to a Mac below the floor is a pool of zero.
    assert broker.pool_machines("voice", KINDS["voice"][1], "machine:m16-01") == {"m16-01"}
    assert broker.pool_machines("voice", KINDS["voice"][1], "machine:m8-01") == set()
    # Offline Macs leave the pool.
    sim.set_online("m24-00", False)
    assert len(broker.pool_machines("chat", KINDS["llm"][1], "pool")) == 2


def test_unknown_ram_counts_as_eligible(sim, monkeypatch):
    monkeypatch.setattr(broker, "_host_for_studio", lambda studio: None)
    assert len(broker.pool_machines("chat", KINDS["llm"][1], "pool")) == 18


# ─────────────────────────────── a Mac someone else holds ───────────────────────────────

def test_a_reserved_peer_is_retried_within_seconds_and_never_left_idle(reset, monkeypatch):
    """A joined node whose own work holds its Mac answers 409 MACHINE_BUSY for 20 s.

    The controller must not park the item for 30 s and the Mac for 90 s: the instant the peer's
    Mac is free again the next dispatch has to reach it. The bound is the avoid window plus one
    heartbeat, and it is the only thing that has to hold - the 409 stays the authority.
    """
    sim = Sim(monkeypatch, fleet=((8, 2),))
    peer = "m8-01"
    freed_at = sim.clock.now + 20.0
    sim.refuse_until[peer] = freed_at

    asyncio.run(sim.run([(0, lambda: sim.submit("image", 12))]))
    assert sim.refusals >= 1, "the scenario never met the peer's refusal"
    started = sorted(job["t"] for job in sim.log if job["machine"] == peer)
    assert started, "the peer never received any work"
    gap = started[0] - freed_at
    bound = broker.MACHINE_RESERVED_AVOID_S + 3.0
    assert 0 <= gap <= bound, (
        f"never idle: the peer's Mac was free for {gap:.0f}s before work reached it "
        f"(bound {bound:.0f}s = the avoid window + one 3 s heartbeat)")


def test_one_refusal_covers_every_item_aimed_at_that_machine(reset, monkeypatch):
    sim = Sim(monkeypatch, fleet=((8, 2),))
    broker._machine_reserved_until["m8-01"] = sim.clock.now + 5.0
    ids = [s["id"] for s in broker._eligible_studios("image", "pool")]
    assert "image@m8-01" not in ids and "image@m8-00" in ids
    sim.clock.now += 6.0
    assert "image@m8-01" in [s["id"] for s in broker._eligible_studios("image", "pool")]


# ─────────────────────────────── requirement: measured -> manifest -> catalog ───────────────────────────────

def test_requirement_order_is_override_then_measured_then_manifest_then_catalog(reset):
    flux = "AITRADER/FLUX2-klein-4B-mlx-4bit"
    row = {"repo": flux, "min_unified_memory_gb": 16}
    # Measured on the fleet's 8 GB Macs beats the catalog's and the manifest's 16 GB.
    assert memory_admission.requirement_gb(flux, row) == (8.0, "measured")
    # No measurement: the manifest's qualification comes before the worker's catalog row.
    qwen = "mlx-community/Qwen3.5-9B-4bit"
    assert memory_admission.requirement_gb(qwen, {"repo": qwen, "min_total_memory_gb": 8}) == (
        16.0, "manifest")
    # Not in any manifest: the catalog.
    other = {"repo": "org/unknown", "min_unified_memory_gb": 24}
    assert memory_admission.requirement_gb("org/unknown", other) == (24.0, "catalog")
    assert memory_admission.requirement_gb("org/none", {"repo": "org/none"}) == (None, "none")
    # An operator override wins over all of it.
    memory_admission.set_override(flux, min_total_memory_gb=12, min_free_memory_gb=2,
                                  catalog_entry=row)
    assert memory_admission.requirement_gb(flux, row) == (12.0, "operator_override")


def test_the_floor_is_a_wall_at_dispatch(reset):
    row = {"repo": "org/other-model", "min_unified_memory_gb": 24}
    requirements = broker._admission_requirements("org/other-model", row)
    assert requirements["min_total"] == 24.0
    assert broker._memory_gate(requirements, {"total_gb": 17.2, "available_gb": 10})[0] == "skip"
    assert broker._memory_gate(requirements, {"total_gb": 25.8, "available_gb": 10})[0] == "run"


def test_published_capacity_counts_only_macs_that_meet_the_requirement(reset, monkeypatch):
    """`eligible_physical_slots_total` is built from `capacity_eligible`, which was RAM-blind."""
    from components.hub.backend import capabilities

    row = {"repo": "org/needs-16", "hub_modality": "voice", "hub_cached": True,
           "min_unified_memory_gb": 16}
    studio = {"id": "voice@x", "modality": "voice", "machine": "x"}
    worker = {"id": "voice@x", "modality": "voice", "online": True, "ready": True,
              "drained": False, "maintenance": False, "machine_quarantined": False,
              "busy": False, "physical_machine_busy": False, "available_capacity": {"slots": 1}}
    verdicts = {}
    for label, total in (("8gb", 8.59), ("16gb", 17.18)):
        monkeypatch.setattr(capabilities, "_machine_host",
                            lambda machine, total=total: (True, {"total_gb": total,
                                                                 "available_gb": 6.0}))
        verdicts[label] = capabilities._model_capability(row, studio, worker)["availability"]
    assert verdicts["8gb"]["capacity_eligible"] is False
    assert verdicts["8gb"]["reason"] == "insufficient_total_memory"
    assert verdicts["16gb"]["capacity_eligible"] is True


def test_older_chat_fifo_finishes_before_newer_voice_without_preference(reset, monkeypatch):
    """Chat and Voice share equal rank by default, so age wins across their queue implementations."""
    kinds = {"image": KINDS["image"], "voice": KINDS["voice"],
             "llm": ("chat", "sim/llm-8gb", 8, 20.0)}
    sim = Sim(monkeypatch, kinds=kinds)
    asyncio.run(sim.run([(0, lambda: sim.submit("llm", 150)), (100, lambda: sim.submit("voice", 60))],
                        chat_first=True))
    assert not sim.idle_violations
    first_voice = min(job["t"] for job in sim.log if job["kind"] == "voice")
    assert max(job["t"] for job in sim.log if job["kind"] == "llm") <= first_voice
    assert not [j for j in sim.log if j["kind"] == "voice" and j["gb"] < 16]


def test_a_transcription_model_has_a_pool_like_every_other_lane(reset, monkeypatch):
    """Subtitle's Whisper rows live in the transcription cache, not the catalog; same floor, same pool."""
    sim = Sim(monkeypatch, fleet=((8, 2), (16, 1)))
    studios = [{"id": f"transcription@{m}", "modality": "transcription", "machine": m,
                "host": "127.0.0.1", "port": 1, "local": False,
                "runtime_identity": {"id": "runtime", "compatible": True}} for m in sim.macs]
    sim.monitor.registry.extend(studios)
    for studio in studios:
        sim.monitor.status[studio["id"]] = {"status": "up"}
        sim.monitor._transcribe_cache[studio["id"]] = (0, {"models": [
            {"repo": "sim/whisper-big", "cached": True, "min_total_memory_gb": 12},
            {"repo": "sim/whisper-small", "cached": True, "min_total_memory_gb": 8},
            {"repo": "sim/whisper-missing", "cached": False, "min_total_memory_gb": 8}]})
    assert broker.pool_machines("transcription", "sim/whisper-big") == {"m16-00"}
    assert len(broker.pool_machines("transcription", "sim/whisper-small")) == 3
    assert broker.pool_machines("transcription", "sim/whisper-missing") == set()
