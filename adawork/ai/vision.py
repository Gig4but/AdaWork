"""Image ads (PLAN §6.8): the vision model writes out the text of a poster or a photo of a
notice, and the text then goes through the same extraction and checks as any other ad.

Keeping one path means the checks still work: a salary is accepted only if it appears in the
transcribed text. The model is loaded with its vision part (`mmproj`) only for this work.
"""

from adawork.ai.client import LlmClient, Reply, image_part

PROMPT_VERSION = 1
MAX_IMAGE_SIDE = 1024  # larger images cost much more time and add little for posters

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
        max_tokens=600,
    )
    text = str(reply.data).strip()
    return Reply("" if text in ("-", "") else text, reply.timing)
