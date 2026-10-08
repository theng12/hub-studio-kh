"""Isolated ranked-function arbitration; no model inference or real library."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

PROOF = r'''
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from components.hub.backend import auth, broker, chat_jobs, chat_priority, fleet_nodes, peers, registry, transcription_jobs

workers = [
 {"id":"image","title":"Image","modality":"image","machine":"local","local":True},
 {"id":"voice","title":"Voice","modality":"voice","machine":"local","local":True},
 {"id":"music","title":"Music","modality":"music","machine":"local","local":True},
 {"id":"chat","title":"Chat","modality":"chat","machine":"local","local":True},
 {"id":"subtitle","title":"Subtitle","modality":"transcription","machine":"local","local":True},
]
monitor = SimpleNamespace(
 registry=workers,
 status={row["id"]:{"status":"up"} for row in workers},
 _client=None,
)

async def catalog(_studio):
 return {"models":[{"repo":"model","cache":{"state":"cached"},
                    "runtime_ready":True,"available":True}]}

monitor.scheduling_catalog = catalog
monitor.cached_catalog_entries = lambda model, modality=None: [
 {"studio":row,"entry":{"repo":model,"cache":{"state":"cached"},
                         "runtime_ready":True,"available":True}}
 for row in monitor.registry if modality in (None,row.get("modality"))]
monitor.scheduling_catalog_entries = monitor.cached_catalog_entries
broker.configure_monitor(monitor)
broker.batches.clear()
broker._busy.clear()
broker._external_machine_leases.clear()
chat_jobs.batches.clear()
chat_jobs.busy_studios.clear()
chat_priority.requests.clear()
transcription_jobs.batches.clear()

async def ordering_proof():
 async def chat_candidate(_monitor, _machine): return (2.0, "chat-batch")
 async def subtitle_candidate(_monitor, _machine): return None
 with patch.object(broker, "_broker_dispatchable_work", return_value=[
       ("voice", 1.0, "broker:voice"),
       ("image", 3.0, "broker:image")]), \
      patch.object(chat_jobs, "has_queued_work", return_value=True), \
      patch.object(chat_jobs, "dispatchable_candidate", new=chat_candidate), \
      patch.object(transcription_jobs, "has_queued_work", return_value=False), \
      patch.object(transcription_jobs, "dispatchable_candidate", new=subtitle_candidate):
  registry.set_priority_order("local", ["image","voice","chat"])
  assert (await broker.next_work("local"))[3] == "image"
  registry.set_priority_order("local", [])
  assert (await broker.next_work("local"))[3] == "voice"
  registry.set_priority_order("local", ["chat"])
  assert (await broker.next_work("local"))[3] == "chat"
  registry.set_priority_order("local", ["transcription"])
  assert (await broker.next_work("local"))[3] == "voice", "omitted functions tie by age"

asyncio.run(ordering_proof())


async def immutable_revision_probe_proof():
 image=workers[0]
 stale={"repo":"model","cache":{"state":"cached"},"runtime_ready":True,
        "available":True,"model_revision":"b"*40}
 monitor.cached_catalog_entries=lambda model, modality=None: [
  {"studio":image,"entry":stale}] if modality in (None,"image") else []
 monitor.scheduling_catalog_entries=monitor.cached_catalog_entries
 broker.batches.clear()
 broker.batches["wrong-revision"]={
  "id":"wrong-revision","created_at":1.0,"cancelled":False,"modality":"image",
  "model":"model","routing":"pool","shared_params":{},
  "genstudio_execution":{"model_revision":"a"*40},
  "items":[{"index":0,"state":"queued","retry_at":None,"params":{},
            "prompt":"x","tries":0,"studio":None,"studio_job_id":None}]}
 direct=chat_priority.register("e"*32,"local","chat","model")
 direct["ready"]=True
 registry.set_priority_order("local",["image","chat"])
 assert (await broker.next_work("local"))[3]=="chat", \
  "a preferred broker batch with the wrong immutable revision blocked eligible Chat"
 chat_priority.finish(direct)
 broker.batches.clear()

asyncio.run(immutable_revision_probe_proof())


async def actual_eligibility_proof():
 """Preferred work only wins after the real lane probe says it can start."""
 catalog_state={"qualified":True,"available_gb":28.0}
 async def changing_catalog(_studio):
  models=[] if not catalog_state["qualified"] else [{
   "repo":"model","cache":{"state":"cached"},"runtime_ready":True,
   "available":True,"min_total_memory_gb":16.0,"min_free_memory_gb":2.0}]
  return {"models":models}
 async def changing_transcription(_studio):
  return {"available":catalog_state["qualified"],"models":([{
   "repo":"model","cached":True,"min_total_memory_gb":16.0,
   "min_free_memory_gb":2.0}] if catalog_state["qualified"] else [])}
 monitor.scheduling_catalog=changing_catalog
 monitor.scheduling_transcription=changing_transcription
 chat_jobs.batches.clear()
 chat_jobs.batches["preferred-chat"]={
  "id":"preferred-chat","created_at":1.0,"cancelled":False,"model":"model",
  "routing":"pool","packs":[{"index":0,"state":"queued","retry_at":None,
  "params":{},"scene_ids":["scene"],"messages":[{"role":"user","content":"x"}],
  "results":{},"tries":0,"studio":None}],"genstudio_execution":None}
 music=[("music",2.0,"broker:music")]
 registry.set_priority_order("local",["chat","transcription","music"])
 with patch.object(broker,"_broker_dispatchable_work",return_value=music), \
      patch.object(broker,"_host_for_studio",side_effect=lambda _studio:{
       "total_gb":32.0,"available_gb":catalog_state["available_gb"]}):
  registry.set_studio_enabled("local","chat",False)
  assert (await broker.next_work("local"))[3]=="music", "disabled preferred Chat blocked Music"
  registry.set_studio_enabled("local","chat",True)
  catalog_state["qualified"]=False
  assert (await broker.next_work("local"))[3]=="music", "unqualified preferred Chat blocked Music"
  catalog_state.update(qualified=True,available_gb=0.5)
  assert (await broker.next_work("local"))[3]=="music", "low-memory preferred Chat blocked Music"
  catalog_state["available_gb"]=28.0
  assert (await broker.next_work("local"))[3]=="chat"

  chat_jobs.batches.clear()
  transcription_jobs.batches.clear()
  transcription_jobs.batches["preferred-subtitle"]={
   "id":"preferred-subtitle","created_at":1.5,"cancelled":False,"model":"model",
   "routing":"pool","items":[{"index":0,"state":"queued","retry_at":None,
   "params":{},"tries":0,"studio":None}],"genstudio_execution":None}
  catalog_state["qualified"]=False
  assert (await broker.next_work("local"))[3]=="music", "unavailable Subtitle blocked Music"
  catalog_state["qualified"]=True
  transcription_jobs.batches["preferred-subtitle"]["routing"]="machine:other"
  assert (await broker.next_work("local"))[3]=="music", "Subtitle pinned elsewhere blocked Music"
  transcription_jobs.batches["preferred-subtitle"]["routing"]="pool"
  assert (await broker.next_work("local"))[3]=="transcription"

  # A preference change never preempts a physical lease. It applies as soon as
  # the current owner releases the Mac and the next atomic reservation occurs.
  transcription_jobs.batches.clear()
  direct=chat_priority.register("f"*32,"local","chat","model")
  direct["ready"]=True
  assert broker.acquire_external_machine("local","running:music")
  registry.set_priority_order("local",["chat","music"])
  assert broker._external_machine_leases["local"]=="running:music"
  assert not broker.acquire_external_machine("local","direct-chat:"+direct["id"])
  broker.release_external_machine("local","running:music")
  assert (await broker.next_work("local"))[3]=="chat"
  assert broker.acquire_external_machine("local","direct-chat:"+direct["id"])
  broker.release_external_machine("local","direct-chat:"+direct["id"])
  chat_priority.finish(direct)

asyncio.run(actual_eligibility_proof())


def chat_batch(created_at=20.0):
 return {"id":"chat-batch","created_at":created_at,"cancelled":False,"model":"model",
         "routing":"pool","packs":[{"index":0,"state":"queued","retry_at":None,
         "params":{},"scene_ids":["scene"],"messages":[{"role":"user","content":"x"}],
         "results":{},"tries":0,"studio":None}],"genstudio_execution":None}

async def actual_chat_dispatch_proof():
 async def hold(*_args):
  await asyncio.Event().wait()
 with patch.object(broker, "_broker_dispatchable_work", return_value=[
       ("image", 10.0, "broker:image-old")]), \
      patch.object(broker, "_host_for_studio", return_value=None), \
      patch.object(broker, "prepare_machine_memory", new=AsyncMock(return_value=("run",None))), \
      patch.object(chat_jobs, "_save"), \
      patch.object(chat_jobs, "_run_pack", new=hold), \
      patch.object(transcription_jobs, "has_queued_work", return_value=False):
  chat_jobs.batches.clear()
  chat_jobs.batches["chat-batch"] = chat_batch()
  registry.set_priority_order("local", ["image","chat"])
  assert await chat_jobs.dispatch_once(monitor) == 0
  assert chat_jobs.batches["chat-batch"]["packs"][0]["state"] == "queued"

  registry.set_priority_order("local", ["chat","image"])
  assert await chat_jobs.dispatch_once(monitor) == 1
  assert chat_jobs.batches["chat-batch"]["packs"][0]["state"] == "running"
  for task in list(chat_jobs._pack_tasks.values()): task.cancel()
  await asyncio.gather(*chat_jobs._pack_tasks.values(), return_exceptions=True)
  chat_jobs._pack_tasks.clear()
  chat_jobs.busy_studios.clear()
  broker._external_machine_leases.clear()

  chat_jobs.batches["chat-batch"] = chat_batch()
  registry.set_priority_order("local", [])
  assert await chat_jobs.dispatch_once(monitor) == 0, "older equal-rank Image wins"

asyncio.run(actual_chat_dispatch_proof())


async def cancellation_during_arbitration_proof():
 monitor.scheduling_catalog=catalog
 async def transcription_catalog(_studio):
  return {"available":True,"models":[{"repo":"model","cached":True,
   "runtime_ready":True,"available":True}]}
 monitor.scheduling_transcription=transcription_catalog
 async def hold(*_args):
  raise AssertionError("cancelled work reached a worker task")

 chat_jobs.batches.clear()
 chat=chat_batch()
 chat_jobs.batches[chat["id"]]=chat
 async def cancel_chat(*_args):
  chat["cancelled"]=True
  chat["packs"][0]["state"]="cancelled"
  return False
 with patch.object(broker,"prepare_machine_memory",new=AsyncMock(return_value=("run",None))), \
      patch.object(broker,"higher_priority_work_waiting",new=cancel_chat), \
      patch.object(chat_jobs,"_save"), patch.object(chat_jobs,"_run_pack",new=hold):
  assert await chat_jobs.dispatch_once(monitor)==0
 assert chat["packs"][0]["tries"]==0 and "local" not in broker._external_machine_leases

 transcription_jobs.batches.clear()
 subtitle={"id":"subtitle-cancel","created_at":20.0,"cancelled":False,"model":"model",
  "routing":"pool","items":[{"index":0,"state":"queued","retry_at":None,
  "params":{},"tries":0,"studio":None}],"genstudio_execution":None}
 transcription_jobs.batches[subtitle["id"]]=subtitle
 async def cancel_subtitle(*_args):
  subtitle["cancelled"]=True
  subtitle["items"][0]["state"]="cancelled"
  return False
 with patch.object(broker,"prepare_machine_memory",new=AsyncMock(return_value=("run",None))), \
      patch.object(broker,"higher_priority_work_waiting",new=cancel_subtitle), \
      patch.object(transcription_jobs,"_save_quietly"), \
      patch.object(transcription_jobs,"_run_item",new=hold):
  assert await transcription_jobs.dispatch_once(monitor)==0
 assert subtitle["items"][0]["tries"]==0 and "local" not in broker._external_machine_leases

asyncio.run(cancellation_during_arbitration_proof())


async def actual_broker_loop_proof():
 image = workers[0]
 batch = {"id":"image-batch","created_at":20.0,"cancelled":False,"modality":"image",
          "model":"model","routing":"pool","shared_params":{},"items":[{
          "index":0,"state":"queued","retry_at":None,"params":{},"prompt":"x",
          "tries":0,"studio":None,"studio_job_id":None}]}
 broker.batches.clear()
 broker.batches[batch["id"]] = batch
 async def entry(_studio, _model):
  return {"repo":"model","cache":{"state":"cached"},"runtime_ready":True,"available":True}
 async def hold(*_args):
  await asyncio.Event().wait()
 async def chat_candidate(_monitor, _machine): return (30.0, "chat-newer")
 async def run_passes():
  task=asyncio.create_task(broker._dispatch_loop())
  for _ in range(30): await asyncio.sleep(0)
  task.cancel()
  await asyncio.gather(task, return_exceptions=True)
 with patch.object(broker, "_queued_batches", return_value=[(1.0,batch)]), \
      patch.object(broker, "_broker_dispatchable_work", return_value=[
       ("image",20.0,"broker:image-batch")]), \
      patch.object(broker, "_eligible_studios", return_value=[image]), \
      patch.object(broker, "_catalog_entry", new=entry), \
      patch.object(broker, "prepare_machine_memory", new=AsyncMock(return_value=("run",None))), \
      patch.object(broker, "admission_refusal", return_value=None), \
      patch.object(broker.ledger, "save_batch"), \
      patch.object(broker, "_run_item", new=hold), \
      patch.object(chat_jobs, "has_queued_work", return_value=True), \
      patch.object(chat_jobs, "dispatchable_candidate", new=chat_candidate), \
      patch.object(transcription_jobs, "has_queued_work", return_value=False):
  registry.set_priority_order("local", ["chat","image"])
  await run_passes()
  assert batch["items"][0]["state"] == "queued"
  registry.set_priority_order("local", ["image","chat"])
  await run_passes()
  assert batch["items"][0]["state"] == "running"
  broker._busy.clear()

  batch.update(cancelled=False)
  batch["items"][0].update(state="queued",tries=0,studio=None)
  async def cancel_broker(*_args):
   batch["cancelled"]=True
   batch["items"][0]["state"]="cancelled"
   return False
  with patch.object(broker,"higher_priority_work_waiting",new=cancel_broker):
   await run_passes()
  assert batch["items"][0]["tries"]==0 and not broker._busy

asyncio.run(actual_broker_loop_proof())


async def relay_proof():
 calls=[]
 async def target(request):
  calls.append((request.method, request.url.path))
  return httpx.Response(200,json={"machine_enabled":True,"priority_order":["voice"],
   "priority_functions":[{"id":"voice","title":"Voice"}],"chat_priority":False,"studios":[]})
 async with httpx.AsyncClient(transport=httpx.MockTransport(target)) as client:
  with patch.object(fleet_nodes,"_read",return_value={"node-a":{"address":"http://node-a"}}), \
       patch.object(peers,"invalidate") as invalidated:
   read=await fleet_nodes.relay_work_policy("node-a","GET",client=client)
   assert read["node"]=="node-a" and read["work_policy"]["priority_order"]==["voice"]
   invalidated.assert_not_called()
   saved=await fleet_nodes.relay_work_policy(
    "node-a","POST",{"priority_order":["voice"]},client=client)
   assert saved["ok"] is True
   invalidated.assert_called_once_with("node-a")
 assert calls==[("GET","/api/hub/work-policy"),("POST","/api/hub/work-policy")]

 async def old_target(_request): return httpx.Response(404,json={"detail":"missing"})
 async with httpx.AsyncClient(transport=httpx.MockTransport(old_target)) as client:
  with patch.object(fleet_nodes,"_read",return_value={"node-a":{"address":"http://node-a"}}):
   try:
    await fleet_nodes.relay_work_policy("node-a","GET",client=client)
    raise AssertionError("old Hub was reported as editable")
   except fleet_nodes.StartupRelayError as error:
    assert error.status==409 and error.body["code"]=="WORK_POLICY_UNSUPPORTED"

 assert auth.fleet_path_allowed("GET","/api/hub/fleet/nodes/node-a/work-policy")
 assert auth.fleet_path_allowed("POST","/api/hub/fleet/nodes/node-a/work-policy")
 assert not auth.node_path_allowed("POST","/api/hub/fleet/nodes/node-a/work-policy")

asyncio.run(relay_proof())
print("ranked arbitration: policy keys and Chat/Broker dispatch paths proved")
'''


class WorkPriorityTests(unittest.TestCase):
    def test_ranked_fifo_arbitrates_actual_dispatch_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-work-priority-") as state:
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith("STUDIOHUB_")}
            env.update(
                STUDIOHUB_DATA_DIR=state,
                STUDIOHUB_MODELS_DIR=str(Path(state) / "models"),
                STUDIOHUB_MODEL_MANIFESTS=str(Path(state) / "manifests"),
                STUDIOHUB_PACKAGED="1",
                PYTHONPATH=str(ROOT),
            )
            runtime = os.environ.get(
                "STUDIOHUB_TEST_RUNTIME", sys.executable)
            result = subprocess.run(
                [runtime, "-c", PROOF], cwd=ROOT, env=env,
                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Chat/Broker dispatch paths proved", result.stdout)


if __name__ == "__main__":
    unittest.main()
