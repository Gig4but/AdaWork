"""Image ads (PLAN §6.8): the vision model writes out the text of a poster or a photo of a
notice, and the text then goes through the same extraction and checks as any other ad.

Keeping one path means the checks still work: a salary is accepted only if it appears in the
transcribed text. The model is loaded with its vision part (`mmproj`) only for this work.
"""

import re

from adawork.ai.client import LlmClient, Reply, image_part

PROMPT_VERSION = 2
MAX_IMAGE_SIDE = 1024  # larger images cost much more time and add little for posters
MAX_REPEATS = 2  # a line the model writes more often in a row is a loop, not the poster

SYSTEM = """Přepiš veškerý čitelný text z obrázku přesně tak, jak je napsaný, řádek po řádku.
Nic nepřidávej, nevysvětluj a nepřekládej. Pokud na obrázku žádný text není, napiš jen: -"""


def shrink(data: bytes, max_side: int = MAX_IMAGE_SIDE) -> tuple[bytes, str]:
    """JPEG bytes no larger than `max_side` px (phones send 4000 px photos)."""
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=88)
    return out.getvalue(), "image/jpeg"


def transcribe(client: LlmClient, data: bytes) -> Reply:
    """The image's text (empty when there is none). Needs a vision-capable launch."""
    jpeg, mime = shrink(data)
    reply = client.chat(
        [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": [image_part(jpeg, mime), {"type": "text", "text": "Přepis:"}],
            },
        ],
        max_tokens=800,  # a full A4 poster; a longer text is kept as far as it got
    )
    return Reply(clean(str(reply.data)), reply.timing)


def clean(text: str) -> str:
    """Without an echoed "Přepis:" and without loops (a real ad picture got "500" written
    400 times); "-" or nothing means the picture has no text."""
    text = re.sub(r"^\s*Přepis:\s*", "", text.strip())
    lines: list[str] = []
    run = 0
    for line in text.split("\n"):
        same = bool(lines) and bool(line.strip()) and line.strip() == lines[-1].strip()
        run = run + 1 if same else 0
        if run < MAX_REPEATS:
            lines.append(line)
    text = "\n".join(lines).strip()
    return "" if text in ("-", "") else text
