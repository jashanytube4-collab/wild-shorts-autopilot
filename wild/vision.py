"""Is this clip really the animal the script is about? Zero-shot CLIP check for stock clips, whose tags are
written by uploaders and are sometimes wrong (a white lioness tagged "tiger, albino").

A clip is rejected only when a *different* animal clearly wins, so rare species that CLIP barely knows are not
thrown away just for scoring low. Disabled (always passes) if torch/open_clip are not installed.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

import cv2
import numpy as np

log = logging.getLogger("wild.vision")

OTHERS = """lion; lioness; tiger; leopard; cheetah; jaguar; cougar; lynx; house cat; dog; wolf; fox; coyote; hyena;
bear; polar bear; panda; raccoon; badger; otter; seal; sea lion; walrus; whale; dolphin; shark; fish; ray;
jellyfish; octopus; squid; crab; lobster; shrimp; starfish; turtle; tortoise; crocodile; alligator; lizard;
iguana; chameleon; snake; frog; toad; salamander; bird; eagle; hawk; owl; vulture; parrot; penguin; flamingo;
duck; swan; goose; heron; pelican; chicken; peacock; hummingbird; butterfly; moth; bee; wasp; ant; beetle;
spider; scorpion; dragonfly; grasshopper; deer; moose; elk; antelope; gazelle; giraffe; zebra; horse; donkey;
cow; buffalo; bison; goat; sheep; pig; boar; camel; llama; elephant; rhinoceros; hippopotamus; gorilla;
chimpanzee; orangutan; monkey; lemur; kangaroo; koala; sloth; rabbit; squirrel; mouse; rat; hedgehog;
porcupine; bat""".replace("\n", " ").split(";")
OTHERS = [o.strip() for o in OTHERS if o.strip()]
JUNK = ["an empty landscape with no animals", "people", "text on a screen", "a cartoon", "a computer generated image"]


@lru_cache(maxsize=1)
def _model():
    if os.environ.get("WILD_VISION", "1") == "0":
        return None
    try:
        import open_clip
        import torch
    except ImportError:
        log.warning("open_clip not installed: stock clips are checked by their tags only")
        return None
    torch.set_num_threads(max(1, (os.cpu_count() or 2)))
    model, _, prep = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai")
    model.eval()
    return model, prep, open_clip.get_tokenizer("ViT-B-32"), torch


@lru_cache(maxsize=64)
def _text(subject: str):
    m = _model()
    if m is None:
        return None, []
    model, _, tok, torch = m
    sw = set(subject.lower().replace("-", " ").split())
    others = [o for o in OTHERS if not (set(o.split()) & sw) and o not in subject.lower()]
    labels = [subject] + others + JUNK
    prompts = [f"a photo of a {l}" if l not in JUNK else f"a photo of {l}" for l in labels]
    with torch.no_grad():
        t = model.encode_text(tok(prompts))
        t = t / t.norm(dim=-1, keepdim=True)
    return t, labels


def check(frames: list[np.ndarray], subject: str) -> tuple[bool, str]:
    """True unless another animal (or junk) clearly beats the subject on most frames."""
    m = _model()
    if m is None or not frames:
        return True, "unchecked"
    model, prep, _, torch = m
    t, labels = _text(subject)
    from PIL import Image
    imgs = torch.stack([prep(Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))) for f in frames])
    with torch.no_grad():
        e = model.encode_image(imgs)
        e = e / e.norm(dim=-1, keepdim=True)
        probs = (100.0 * e @ t.T).softmax(dim=-1).numpy()
    bad = 0
    why = ""
    for p in probs:
        sub = float(p[0])
        j = int(np.argmax(p[1:])) + 1
        if p[j] > 0.40 and p[j] > 3.0 * sub:
            bad += 1
            why = f"looks like {labels[j]} ({p[j]:.2f} vs {sub:.2f})"
    return bad * 2 <= len(frames) - 1 if len(frames) > 1 else bad == 0, why
