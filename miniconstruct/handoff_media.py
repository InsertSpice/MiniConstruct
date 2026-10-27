"""Bounded local, video-only tail extraction in a killable worker."""
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess
import sys
from threading import BoundedSemaphore

CONTRACT = "v1-h264-crf18-fast-threads2-yuv420p-960-uspts-noaudio"
MAX_BYTES = 32 * 1024 * 1024
MAX_SECONDS = 600
MAX_FRAMES = 36000
TIMEOUT = 120
WORKER_CAPACITY = 2
WORKERS = BoundedSemaphore(WORKER_CAPACITY)


class _BoundedOutput:
    """Enforce temporary-file size even while the encoder flushes buffered packets."""
    def __init__(self, stream):
        self.stream = stream

    def write(self, data):
        if self.stream.tell() + len(data) > MAX_BYTES:
            raise ValueError("Tail exceeds output size limit")
        return self.stream.write(data)

    def seek(self, offset, whence=0):
        return self.stream.seek(offset, whence)

    def tell(self):
        return self.stream.tell()


def extract_tail(source, destination, seconds):
    if not .5 <= seconds <= 2 or not math.isfinite(seconds):
        raise ValueError("Tail duration must be 0.5–2.0 seconds")
    if not WORKERS.acquire(timeout=TIMEOUT):
        raise ValueError("Visual handoff workers are busy; retry shortly")
    try:
        subprocess.run([sys.executable, "-m", "miniconstruct.handoff_media", str(source),
                        str(destination), str(seconds)], shell=False, check=True, timeout=TIMEOUT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not 0 < Path(destination).stat().st_size <= MAX_BYTES:
            raise ValueError("Tail exceeds output size limit")
        return json.loads(Path(str(destination) + ".json").read_text())
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        raise ValueError("Visual handoff could not be decoded safely within 120 seconds; check or reattach the source Take.") from exc
    finally:
        WORKERS.release()


def _frames(source):
    import av
    with open(source, "rb") as raw, av.open(raw, options={"protocol_whitelist": "",
            "format_whitelist": "mov,matroska,webm,avi,mpeg,mpegvideo"}) as container:
        video = container.streams.video[0]
        video.codec_context.thread_count = 2
        previous, origin = None, None
        for count, frame in enumerate(container.decode(video)):
            if count >= MAX_FRAMES or frame.width * frame.height > 4096 * 4096:
                raise ValueError("Source exceeds bounded decoding limits")
            stamp = float(frame.time) if frame.time is not None else float("nan")
            if not math.isfinite(stamp) or (previous is not None and stamp <= previous):
                raise ValueError("Source needs reliable increasing timestamps")
            origin = stamp if origin is None else origin
            if stamp - origin > MAX_SECONDS:
                raise ValueError("Source exceeds ten minute decoding limit")
            previous = stamp
            yield frame, stamp - origin


def _decode(source, destination, seconds):
    import av
    if not math.isfinite(seconds) or not .5 <= seconds <= 2:
        raise ValueError("Tail duration must be 0.5–2.0 seconds")
    # First pass holds no frame history and finds the actual final decodable frame.
    end, previous, step = None, None, .04
    for frame, stamp in _frames(source):
        if frame.duration and frame.time_base:
            step = float(frame.duration * frame.time_base)
        elif previous is not None:
            step = stamp - previous
        previous, end = stamp, stamp
    if end is None:
        raise ValueError("No decodable frames")
    duration = end + step
    if not 0 < step <= MAX_SECONDS or duration > MAX_SECONDS:
        raise ValueError("Source duration exceeds decoding limits")
    start = max(0, duration - seconds)
    first, last, count = None, None, 0
    with open(destination, "w+b") as raw, av.open(_BoundedOutput(raw), "w", format="mp4") as output:
        stream = output.add_stream("libx264", rate=Fraction(1 / min(step, seconds)).limit_denominator(1000000))
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "18", "preset": "fast", "threads": "2"}
        stream.time_base = Fraction(1, 1000000)
        stream.codec_context.time_base = Fraction(1, 1000000)
        for frame, stamp in _frames(source):
            frame_duration = float(frame.duration * frame.time_base) if frame.duration and frame.time_base else step
            if stamp + frame_duration <= start + .000001:
                continue
            stamp = max(stamp, start)
            if first is None:
                first = stamp
                scale = min(1, 960 / max(frame.width, frame.height))
                stream.width = max(2, int(frame.width * scale) // 2 * 2)
                stream.height = max(2, int(frame.height * scale) // 2 * 2)
            count += 1
            if count > 480:
                raise ValueError("Tail exceeds 480 decoded frames")
            frame = frame.reformat(width=stream.width, height=stream.height, format="yuv420p")
            frame.pts = round((stamp - first) * 1000000)
            frame.time_base = stream.time_base
            for packet in stream.encode(frame):
                output.mux(packet)
            last = stamp
            if Path(destination).exists() and Path(destination).stat().st_size > MAX_BYTES:
                raise ValueError("Tail exceeds size limit")
        for packet in stream.encode():
            output.mux(packet)
    if first is None:
        raise ValueError("No tail frames")
    metadata = {"startSeconds": first, "endSeconds": last, "effectiveTailSeconds": min(seconds, duration - first),
                "sourceDurationSeconds": duration, "frameCount": count}
    Path(str(destination) + ".json").write_text(json.dumps(metadata))


if __name__ == "__main__":
    _decode(sys.argv[1], sys.argv[2], float(sys.argv[3]))
