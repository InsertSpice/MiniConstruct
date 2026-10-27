"""Generic deterministic geometric Subject 1 and motion for opt-in review."""
from PIL import Image, ImageDraw


def picture(index=0):
    canvas = Image.new("RGB", (320, 180), "#d7e6eb")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 130, 320, 180), fill="#485966")
    draw.rectangle((240, 30, 295, 130), fill="#718b91")
    x = 40 + index * 3
    draw.ellipse((x - 10, 38, x + 10, 58), fill="#e0b899")
    draw.rectangle((x - 12, 58, x + 12, 104), fill="#335fba")
    stride = (index % 12 - 6) * 2
    draw.line((x - 6, 104, x - 6 + stride, 132), fill="#263447", width=7)
    draw.line((x + 6, 104, x + 6 - stride, 132), fill="#263447", width=7)
    draw.line((x + 12, 68, x + 26, 85), fill="#e0b899", width=5)
    draw.rectangle((x + 23, 81, x + 36, 94), fill="#dfb747")
    return canvas


def video(path):
    import av
    with av.open(str(path), "w") as output:
        stream = output.add_stream("libx264", rate=10)
        stream.width = 320; stream.height = 180; stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "18", "threads": "1"}
        for index in range(40):
            for packet in stream.encode(av.VideoFrame.from_image(picture(index))):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
