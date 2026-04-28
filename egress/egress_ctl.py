"""
Egress Controller — Videolog POC
Contrôle les enregistrements LiveKit Egress via l'API REST.

Utilisation :
  python egress_ctl.py start  <room_name>   [--layout speaker|grid]
  python egress_ctl.py stop   <egress_id>
  python egress_ctl.py list
  python egress_ctl.py status <egress_id>
  python egress_ctl.py watch  <room_name>   # démarre + surveille jusqu'à Ctrl-C
"""
import sys
import os
import json
import asyncio
import signal
import logging
from datetime import datetime

from livekit import api
from livekit.protocol import egress as proto_egress

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [EGRESS-CTL] %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

LIVEKIT_URL    = os.environ.get("LIVEKIT_URL",       "http://livekit:7880")
API_KEY        = os.environ.get("LIVEKIT_API_KEY",    "devkey")
API_SECRET     = os.environ.get("LIVEKIT_API_SECRET", "secret")
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT",     "http://minio:9000")
MINIO_ACCESS   = os.environ.get("MINIO_ACCESS_KEY",   "videolog")
MINIO_SECRET   = os.environ.get("MINIO_SECRET_KEY",   "videolog123")
BUCKET         = "recordings"
MINIO_PUBLIC_URL = os.environ.get("MINIO_PUBLIC_URL", "http://localhost:9000")


def make_s3_upload(room_name: str) -> proto_egress.S3Upload:
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return proto_egress.S3Upload(
        access_key=MINIO_ACCESS,
        secret=MINIO_SECRET,
        bucket=BUCKET,
        endpoint=MINIO_ENDPOINT,
        region="us-east-1",          # ✅ AJOUT CRITIQUE
        force_path_style=True,
    )


def status_label(status_code: int) -> str:
    try:
        return proto_egress.EgressStatus.Name(status_code)
    except Exception:
        return str(status_code)


async def start_recording(room_name: str, layout: str = "speaker") -> str:
    lk = api.LiveKitAPI(LIVEKIT_URL, API_KEY, API_SECRET)

    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    request = proto_egress.RoomCompositeEgressRequest(
        room_name=room_name,
        layout=layout,
        audio_only=False,
        video_only=False,
        file=proto_egress.EncodedFileOutput(
            file_type=1,
            filepath=f"{room_name}/{ts}/recording",
            s3=make_s3_upload(room_name),
        ),
        advanced=proto_egress.EncodingOptions(
            width=1280, height=720, depth=24, framerate=24,
            audio_codec=2, audio_bitrate=128, audio_frequency=44100,
            video_codec=1, video_bitrate=2500, key_frame_interval=4,
        ),
    )

    try:
        info = await lk.egress.start_room_composite_egress(request)
        egress_id = info.egress_id
        log.info("=" * 55)
        log.info("  ENREGISTREMENT DEMARRE")
        log.info(f"  egress_id : {egress_id}")
        log.info(f"  room      : {info.room_name}")
        log.info(f"  layout    : {layout}")
        log.info(f"  status    : {status_label(info.status)}")
        log.info(f"  output    : s3://{BUCKET}/{room_name}/")
        log.info("=" * 55)
        print(json.dumps({
            "egress_id": egress_id,
            "room_name": info.room_name,
            "layout":    layout,
            "status":    status_label(info.status),
            "output":    f"s3://{BUCKET}/{room_name}/",
        }, indent=2))
        return egress_id
    except Exception as e:
        log.error(f"Erreur start_recording: {e}")
        raise
    finally:
        await lk.aclose()


async def stop_recording(egress_id: str):
    lk = api.LiveKitAPI(LIVEKIT_URL, API_KEY, API_SECRET)
    try:
        info = await lk.egress.stop_egress(
            proto_egress.StopEgressRequest(egress_id=egress_id)
        )
        log.info("=" * 55)
        log.info("  ENREGISTREMENT ARRETE")
        log.info(f"  egress_id : {egress_id}")
        log.info(f"  status    : {status_label(info.status)}")
        if info.file_results:
            for f in info.file_results:
                log.info(f"  fichier   : {f.filename}  ({f.size/(1024*1024):.2f} MB)")
        log.info("=" * 55)
        result = {
            "egress_id": info.egress_id,
            "room_name": info.room_name,
            "status":    status_label(info.status),
            "files":     [{"filename": f.filename, "size_bytes": f.size,
                           "duration_s": f.duration,
                           "minio_url": f"{MINIO_PUBLIC_URL}/{BUCKET}/{f.filename}"}
                          for f in (info.file_results or [])],
        }
        print(json.dumps(result, indent=2))
    except Exception as e:
        log.error(f"Erreur stop_recording: {e}")
        raise
    finally:
        await lk.aclose()


async def list_recordings():
    lk = api.LiveKitAPI(LIVEKIT_URL, API_KEY, API_SECRET)
    try:
        resp = await lk.egress.list_egress(proto_egress.ListEgressRequest())
        if not resp.items:
            log.info("Aucun enregistrement actif.")
            print("[]")
            return
        for item in resp.items:
            log.info(f"  [{status_label(item.status):30s}] {item.egress_id}  room={item.room_name}")
        print(json.dumps([{
            "egress_id": i.egress_id, "room_name": i.room_name,
            "status": status_label(i.status), "error": i.error or None,
        } for i in resp.items], indent=2))
    except Exception as e:
        log.error(f"Erreur list: {e}")
    finally:
        await lk.aclose()


async def get_status(egress_id: str):
    lk = api.LiveKitAPI(LIVEKIT_URL, API_KEY, API_SECRET)
    try:
        resp = await lk.egress.list_egress(
            proto_egress.ListEgressRequest(egress_id=egress_id))
        if not resp.items:
            log.warning(f"Egress {egress_id} introuvable.")
            return
        item = resp.items[0]
        print(json.dumps({
            "egress_id": item.egress_id, "room_name": item.room_name,
            "status": status_label(item.status), "error": item.error or None,
        }, indent=2))
    except Exception as e:
        log.error(f"Erreur status: {e}")
    finally:
        await lk.aclose()


async def watch_recording(room_name: str, layout: str = "speaker"):
    egress_id = await start_recording(room_name, layout)
    log.info("Enregistrement en cours... Ctrl-C pour arreter.")
    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT,  lambda: stop_event.set())
    loop.add_signal_handler(signal.SIGTERM, lambda: stop_event.set())

    async def _poll():
        while not stop_event.is_set():
            await asyncio.sleep(5)
            try:
                lk = api.LiveKitAPI(LIVEKIT_URL, API_KEY, API_SECRET)
                resp = await lk.egress.list_egress(
                    proto_egress.ListEgressRequest(egress_id=egress_id))
                await lk.aclose()
                if resp.items:
                    s = status_label(resp.items[0].status)
                    log.info(f"[{s}] {egress_id}")
                    if "ENDED" in s or "FAILED" in s:
                        stop_event.set()
            except Exception:
                pass

    await asyncio.gather(stop_event.wait(), _poll(), return_exceptions=True)
    await stop_recording(egress_id)


def usage():
    print(__doc__)
    sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        usage()
    cmd  = sys.argv[1]
    args = sys.argv[2:]
    layout = "speaker"
    if "--layout" in args:
        idx    = args.index("--layout")
        layout = args[idx + 1]
        args   = [a for i, a in enumerate(args) if i not in (idx, idx + 1)]

    if cmd == "start"  and len(args) == 1: asyncio.run(start_recording(args[0], layout))
    elif cmd == "stop"   and len(args) == 1: asyncio.run(stop_recording(args[0]))
    elif cmd == "list":                       asyncio.run(list_recordings())
    elif cmd == "status" and len(args) == 1: asyncio.run(get_status(args[0]))
    elif cmd == "watch"  and len(args) == 1: asyncio.run(watch_recording(args[0], layout))
    else: usage()
