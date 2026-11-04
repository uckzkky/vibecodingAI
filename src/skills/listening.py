"""用音频分类模型听采样手法和乐器。不检索网页，也不猜测原曲歌名。"""

from __future__ import annotations

import os
from pathlib import Path

from skills.common import tool_error
from skills.schemas import ListenArgs

MODEL_ID = "MIT/ast-finetuned-audioset-10-10-0.4593"
SAMPLE_RATE = 16000
MAX_SECONDS = 30
_pipeline = None

INSTRUMENT_CUES = (
    "acoustic guitar",
    "bass guitar",
    "electric guitar",
    "steel guitar",
    "guitar",
    "piano",
    "electric piano",
    "drum kit",
    "drum machine",
    "snare drum",
    "bass drum",
    "hi-hat",
    "cymbal",
    "drum",
    "synthesizer",
    "violin",
    "cello",
    "organ",
    "hammond organ",
    "saxophone",
    "trumpet",
    "trombone",
    "flute",
    "clarinet",
    "harmonica",
    "harp",
    "accordion",
    "banjo",
    "mandolin",
    "marimba",
    "timpani",
    "singing",
    "rapping",
    "choir",
    "yodeling",
    "chant",
)
SAMPLE_CUES = (
    "scratching (performance technique)",
    "sampler",
    "turntablism",
    "vinyl",
    "sound effect",
)
NAMES = {
    "acoustic guitar": "原声吉他",
    "bass guitar": "贝斯",
    "electric guitar": "电吉他",
    "steel guitar": "钢棒吉他",
    "guitar": "吉他",
    "piano": "钢琴",
    "electric piano": "电钢琴",
    "drum kit": "架子鼓",
    "drum machine": "鼓机",
    "snare drum": "军鼓",
    "bass drum": "底鼓",
    "hi-hat": "踩镲",
    "cymbal": "镲",
    "drum": "鼓",
    "synthesizer": "合成器",
    "violin": "小提琴",
    "cello": "大提琴",
    "organ": "风琴",
    "hammond organ": "风琴",
    "saxophone": "萨克斯",
    "trumpet": "小号",
    "trombone": "长号",
    "flute": "长笛",
    "clarinet": "单簧管",
    "harmonica": "口琴",
    "harp": "竖琴",
    "accordion": "手风琴",
    "banjo": "班卓琴",
    "mandolin": "曼陀林",
    "marimba": "马林巴",
    "timpani": "定音鼓",
    "singing": "人声",
    "rapping": "说唱人声",
    "choir": "合唱",
    "yodeling": "约德尔人声",
    "chant": "吟唱",
    "scratching (performance technique)": "刮碟",
    "sampler": "采样器",
    "turntablism": "搓碟",
    "vinyl": "黑胶质感",
    "sound effect": "音效",
}


def listen_recording(audio_path: str = "") -> dict:
    args = ListenArgs(audio_path=audio_path)
    path = Path(args.audio_path).expanduser() if args.audio_path else None
    if path is None or not path.is_file():
        return {
            "ok": True,
            "audio": "",
            "model": MODEL_ID,
            "instruments": [],
            "sample_cues": [],
            "instrument_reading": "",
            "sample_reading": "",
            "note": "没有音频。采样和乐器只由模型听录音判断，这次没有检索网页。",
        }
    if path.stat().st_size > 40_000_000:
        return tool_error("音频超过 40MB", audio=str(path), model=MODEL_ID)
    try:
        audio = _load(path)
        labels = _classify(audio)
    except Exception as exc:
        return tool_error(f"{type(exc).__name__}: {exc}", audio=str(path), model=MODEL_ID)

    instruments = _pick(labels, INSTRUMENT_CUES)
    samples = _pick(labels, SAMPLE_CUES)
    return {
        "ok": True,
        "audio": str(path),
        "model": MODEL_ID,
        "instruments": instruments,
        "sample_cues": samples,
        "instrument_reading": _instrument_reading(instruments),
        "sample_reading": _sample_reading(samples),
        "note": "",
    }


def _instrument_reading(items: list[dict]) -> str:
    if not items:
        return (
            f"{MODEL_ID} 听完这段录音后，没有哪件乐器的分数超过阈值。"
            "这是音频分类结果，不是发行页上的乐手署名。"
        )
    heard = "、".join(f"{item['label']} {item['score']:.2f}" for item in items)
    return f"{MODEL_ID} 从录音里听出这些乐器或人声：{heard}。这是模型分类，不是乐手名单。"


def _sample_reading(items: list[dict]) -> str:
    if not items:
        return (
            f"{MODEL_ID} 没有听出刮碟、采样器或黑胶循环这类声音。"
            "它不能比对曲库，所以不会给出被采样的歌名。这次没有检索 WhoSampled。"
        )
    heard = "、".join(f"{item['label']} {item['score']:.2f}" for item in items)
    return (
        f"{MODEL_ID} 听出和采样手法有关的声音：{heard}。"
        "这只说明手法，不能对应到某一首原曲，也不是网页检索。"
    )


def _pick(labels: list[dict], cues: tuple[str, ...]) -> list[dict]:
    chosen = []
    seen: set[str] = set()
    for item in labels:
        name = (item.get("label") or "").strip()
        folded = name.casefold()
        if not any(cue in folded for cue in cues):
            continue
        score = float(item.get("score") or 0)
        if score < 0.08:
            continue
        zh = NAMES.get(folded, name)
        if zh in seen:
            continue
        seen.add(zh)
        chosen.append({"label": zh, "score": round(score, 3), "tag": name})
        if len(chosen) >= 6:
            break
    return chosen


def _classify(audio) -> list[dict]:
    result = _get_pipeline()({"array": audio, "sampling_rate": SAMPLE_RATE}, top_k=30)
    if isinstance(result, dict):
        return [result]
    return list(result or [])


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        from transformers import pipeline

        _pipeline = pipeline("audio-classification", model=MODEL_ID)
    return _pipeline


def _load(path: Path):
    import numpy as np

    if path.suffix.lower() == ".mp3":
        import miniaudio

        decoded = miniaudio.decode_file(
            str(path),
            nchannels=1,
            sample_rate=SAMPLE_RATE,
        )
        audio = np.asarray(decoded.samples, dtype=np.float32)
        if audio.size and np.max(np.abs(audio)) > 2:
            audio = audio / 32768.0
        return audio[: SAMPLE_RATE * MAX_SECONDS]

    import soundfile as sf

    data, rate = sf.read(str(path), always_2d=True)
    mono = data.mean(axis=1).astype(np.float32)
    mono = mono[: rate * MAX_SECONDS]
    if rate == SAMPLE_RATE or len(mono) < 2:
        return mono
    target = int(len(mono) * SAMPLE_RATE / rate)
    old = np.linspace(0, 1, num=len(mono), endpoint=False)
    new = np.linspace(0, 1, num=max(target, 1), endpoint=False)
    return np.interp(new, old, mono).astype(np.float32)
