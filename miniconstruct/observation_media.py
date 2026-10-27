"""Local bounded decoding in a killable worker. No shell, no canonical transcoding."""
from collections import deque
import hashlib
import math
from pathlib import Path
import subprocess
import sys
import tempfile

from miniconstruct.models.observations import ObservationEvidence

MAX_IMAGE_BYTES = 1024 * 1024
EXTRACTION_TIMEOUT = 120


def digest(data):
    return hashlib.sha256(data).hexdigest()


def extract_evidence(source):
    from miniconstruct.handoff_media import WORKERS
    if not WORKERS.acquire(timeout=EXTRACTION_TIMEOUT):
        raise ValueError("Local media workers are busy; retry shortly")
    try:
        return _extract_evidence(source)
    finally:
        WORKERS.release()


def _extract_evidence(source):
    with tempfile.TemporaryDirectory(prefix="miniconstruct-observation-") as temporary:
        try:
            subprocess.run([sys.executable, "-m", "miniconstruct.observation_media", str(source), temporary],
                           shell=False, check=True, timeout=EXTRACTION_TIMEOUT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            directory = Path(temporary)
            metadata = ObservationEvidence.model_validate_json((directory / "evidence.json").read_text())
            images = {name: (directory / f"{name}.jpg").read_bytes() for name in ("endpoint", "contact-sheet")}
            if any(len(data) > MAX_IMAGE_BYTES for data in images.values()):
                raise ValueError("Evidence image exceeds size limit")
            return metadata, images
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            raise ValueError("Video could not be decoded safely within 120 seconds. Check the video or import another Take.") from exc


def _decode(source, destination):
    import av
    from PIL import Image, ImageDraw, ImageOps

    samples = deque(maxlen=10)
    last = None
    origin = None
    # File object prevents demuxers treating a supplied filename as a URL.
    with open(source, "rb") as stream, av.open(stream, options={"protocol_whitelist": "",
            "format_whitelist": "mov,matroska,webm,avi,mpeg,mpegvideo"}) as container:
        video = container.streams.video[0]
        video.thread_type = "SLICE"
        video.codec_context.thread_count = 2
        if video.width * video.height > 4096 * 4096:
            raise ValueError("Video dimensions exceed decoding limit")
        for count, frame in enumerate(container.decode(video)):
            if count >= 100000 or frame.width * frame.height > 4096 * 4096:
                raise ValueError("Video exceeds bounded decoding limits")
            if frame.time is None or not math.isfinite(float(frame.time)):
                raise ValueError("Video lacks reliable frame timestamps")
            if origin is None:
                origin = float(frame.time)
            stamp = round(float(frame.time) - origin, 6)
            if stamp > 600:
                raise ValueError("Video exceeds ten minute decoding limit")
            if stamp < 0 or (last and stamp < last[0]):
                raise ValueError("Video has nonmonotonic frame timestamps")
            scale = min(1, 960 / max(frame.width, frame.height))
            picture = frame.to_image(width=max(1, round(frame.width * scale)), height=max(1, round(frame.height * scale)))
            last = (stamp, picture)
            if not samples or stamp - samples[-1][0] >= .24:
                samples.append(last)
            while samples and samples[0][0] < stamp - 2:
                samples.popleft()
    if last is None:
        raise ValueError("No decodable video frames")
    end = last[0]
    available = [s for s in samples if s[0] >= max(0, end - 2)] + [last]
    chosen = {}
    for offset in (1.5, 1, .5):
        stamp, picture = min(available, key=lambda pair: abs(pair[0] - max(0, end - offset)))
        chosen[stamp] = picture
    chosen[end] = last[1]
    chosen = sorted(chosen.items())
    directory = Path(destination)
    last[1].save(directory / "endpoint.jpg", quality=85, optimize=True)
    sheet = Image.new("RGB", (960, 588), "#15171b")
    draw = ImageDraw.Draw(sheet)
    for index, (stamp, picture) in enumerate(chosen):
        x, y = (index % 2) * 480, (index // 2) * 294
        tile = ImageOps.contain(picture, (480, 270))
        sheet.paste(tile, (x + (480 - tile.width) // 2, y))
        draw.text((x + 8, y + 274), f"{stamp:.3f}s" + (" ENDPOINT" if stamp == end else ""), fill="white")
    sheet.save(directory / "contact-sheet.jpg", quality=82, optimize=True)
    metadata = ObservationEvidence(sampledTimestamps=[t for t, _ in chosen], endpointTimestamp=end,
        endpointSha256=digest((directory / "endpoint.jpg").read_bytes()),
        contactSheetSha256=digest((directory / "contact-sheet.jpg").read_bytes()))
    (directory / "evidence.json").write_text(metadata.model_dump_json(by_alias=True), encoding="utf-8")


if __name__ == "__main__":
    _decode(sys.argv[1], sys.argv[2])
