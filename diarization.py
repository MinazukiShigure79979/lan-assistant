"""Offline speaker diarization using sherpa-onnx.

The app never ships diarization weights.  The user selects an embedding model
in Settings and the two required ONNX files (pyannote segmentation + speaker
embedding) are downloaded into the local models directory on demand.

The pipeline works on already VAD-segmented meeting turns: final turn audio is
kept only in memory for the duration of the meeting, concatenated with short
silence gaps, clustered globally, then mapped back to turn ids.
"""
from __future__ import annotations

import os
import tarfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

import config

DIARIZATION_DIR = config.MODELS_DIR / "diarization"
SEGMENTATION_DIR = DIARIZATION_DIR / "sherpa-onnx-pyannote-segmentation-3-0"
SEGMENTATION_MODEL = SEGMENTATION_DIR / "model.onnx"
SEGMENTATION_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
SEGMENTATION_FALLBACK_URL = (
    "https://hf-mirror.com/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/"
    "resolve/main/model.onnx"
)

EMBEDDING_MODELS: dict[str, dict[str, Any]] = {
    "wespeaker_en_voxceleb_resnet34": {
        "id": "wespeaker_en_voxceleb_resnet34",
        "label": "WeSpeaker ResNet34（推荐，多语种）",
        "short": "WeSpeaker",
        "desc": "体积小、跨语种稳，适合英语/日语/韩语和一般会议；中英日混合首选。",
        "file": "wespeaker_en_voxceleb_resnet34_LM.onnx",
        "url": (
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx"
        ),
        "fallback_url": (
            "https://hf-mirror.com/chengdongliang/wespeaker/resolve/main/"
            "voxceleb_resnet34_LM.onnx"
        ),
        "needs_metadata": True,
        "size": "26 MB",
        "license": "CC-BY-4.0",
    },
}
DEFAULT_MODEL = "wespeaker_en_voxceleb_resnet34"

_lock = threading.RLock()
_state: dict[str, Any] = {
    "status": "idle",
    "progress": 0,
    "message": "",
    "model": DEFAULT_MODEL,
    "error": "",
    "started_at": 0.0,
    "finished_at": 0.0,
}


def _ensure_dir() -> None:
    DIARIZATION_DIR.mkdir(parents=True, exist_ok=True)


def embedding_path(model_id: str) -> Path:
    info = EMBEDDING_MODELS.get(model_id) or EMBEDDING_MODELS[DEFAULT_MODEL]
    return DIARIZATION_DIR / info["file"]


def is_ready(model_id: str = DEFAULT_MODEL) -> bool:
    return _sherpa_available() and SEGMENTATION_MODEL.is_file() and embedding_path(model_id).is_file()


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except Exception:
        return 0


def catalog(selected: str = DEFAULT_MODEL) -> dict[str, Any]:
    models = []
    for model_id, info in EMBEDDING_MODELS.items():
        path = embedding_path(model_id)
        models.append(
            {
                "id": model_id,
                "label": info["label"],
                "short": info["short"],
                "desc": info["desc"],
                "size": info["size"],
                "license": info["license"],
                "downloaded": path.is_file() and _file_size(path) > 1_000_000,
                "selected": model_id == selected,
                "bytes": _file_size(path),
            }
        )
    return {
        "models": models,
        "selected": selected,
        "segmentation_downloaded": SEGMENTATION_MODEL.is_file(),
        "ready": is_ready(selected),
        "status": dict(_state),
        "dir": str(DIARIZATION_DIR),
    }


def public_state(selected: str = DEFAULT_MODEL) -> dict[str, Any]:
    data = catalog(selected)
    data["library"] = _sherpa_available()
    return data


def _sherpa_available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception:
        return False


def start_component_download(component_id: str) -> dict[str, Any]:
    """Download one preset diarization file (used by model management rows)."""
    component_id = str(component_id or "")
    if component_id == "diarization-segmentation":
        kind = "segmentation"
        model_id = DEFAULT_MODEL
        target = SEGMENTATION_MODEL
    elif component_id.startswith("diarization-"):
        model_id = component_id[len("diarization-"):]
        if model_id not in EMBEDDING_MODELS:
            return {"ok": False, "error": "找不到这个声纹模型"}
        kind = "embedding"
        target = embedding_path(model_id)
    else:
        return {"ok": False, "error": "未知的说话人区分模型"}
    if target.is_file() and _file_size(target) > 1_000_000:
        return {"ok": True, "started": False, "message": "模型已经存在"}
    with _lock:
        if _state.get("status") in ("downloading", "extracting"):
            return {"ok": False, "error": "已有说话人区分模型正在下载"}
        _state.update(
            {
                "status": "downloading",
                "progress": 0,
                "message": "正在下载单个模型文件…",
                "model": component_id,
                "error": "",
                "started_at": time.time(),
                "finished_at": 0.0,
            }
        )
    thread = threading.Thread(
        target=_component_worker,
        args=(kind, model_id),
        name="diarization-component-download",
        daemon=True,
    )
    thread.start()
    return {"ok": True, "started": True, "state": dict(_state)}


def _component_worker(kind: str, model_id: str) -> None:
    try:
        _ensure_dir()
        if kind == "segmentation":
            _download_segmentation()
        else:
            _download_embedding(model_id)
        with _lock:
            _state.update(
                {
                    "status": "ready",
                    "progress": 100,
                    "message": "模型文件已就绪",
                    "finished_at": time.time(),
                }
            )
    except Exception as exc:
        with _lock:
            _state.update(
                {
                    "status": "error",
                    "message": "模型文件下载失败",
                    "error": str(exc)[:500],
                    "finished_at": time.time(),
                }
            )


def _download_segmentation() -> None:
    if SEGMENTATION_MODEL.is_file():
        return
    archive = DIARIZATION_DIR / "segmentation.tar.bz2"
    try:
        _download(SEGMENTATION_URL, archive, 0, 40)
        with _lock:
            _state.update({"status": "extracting", "progress": 42, "message": "正在解压分割模型…"})
        _extract(archive, DIARIZATION_DIR)
    except Exception:
        with _lock:
            _state.update({"progress": 40, "message": "官方下载较慢，正在切换镜像…"})
        SEGMENTATION_DIR.mkdir(parents=True, exist_ok=True)
        _download(SEGMENTATION_FALLBACK_URL, SEGMENTATION_MODEL, 40, 45)
    finally:
        archive.unlink(missing_ok=True)
    if not SEGMENTATION_MODEL.is_file():
        raise RuntimeError("分割模型解压后没有找到 model.onnx")


def _download_embedding(model_id: str) -> None:
    info = EMBEDDING_MODELS[model_id]
    embedding = embedding_path(model_id)
    if embedding.is_file() and _file_size(embedding) > 1_000_000:
        return
    try:
        _download(info["url"], embedding, 45, 100)
    except Exception:
        fallback = info.get("fallback_url")
        if not fallback:
            raise
        with _lock:
            _state.update({"progress": 45, "message": "官方下载较慢，正在切换镜像…"})
        _download(fallback, embedding, 45, 100)
    if info.get("needs_metadata"):
        _ensure_wespeaker_metadata(embedding)


def start_download(model_id: str = DEFAULT_MODEL) -> dict[str, Any]:
    model_id = model_id if model_id in EMBEDDING_MODELS else DEFAULT_MODEL
    with _lock:
        if _state.get("status") in ("downloading", "extracting"):
            return {"ok": True, "started": False, "state": dict(_state)}
        _state.update(
            {
                "status": "downloading",
                "progress": 0,
                "message": "正在下载说话人区分模型…",
                "model": model_id,
                "error": "",
                "started_at": time.time(),
                "finished_at": 0.0,
            }
        )
    thread = threading.Thread(
        target=_download_worker,
        args=(model_id,),
        name="diarization-download",
        daemon=True,
    )
    thread.start()
    return {"ok": True, "started": True, "state": dict(_state)}


def _download_worker(model_id: str) -> None:
    try:
        _ensure_dir()
        if not SEGMENTATION_MODEL.is_file():
            archive = DIARIZATION_DIR / "segmentation.tar.bz2"
            try:
                _download(SEGMENTATION_URL, archive, 0, 40)
                with _lock:
                    _state.update({"status": "extracting", "progress": 42, "message": "正在解压分割模型…"})
                _extract(archive, DIARIZATION_DIR)
            except Exception:
                with _lock:
                    _state.update(
                        {
                            "progress": 40,
                            "message": "官方下载较慢，正在切换镜像…",
                        }
                    )
                SEGMENTATION_DIR.mkdir(parents=True, exist_ok=True)
                _download(SEGMENTATION_FALLBACK_URL, SEGMENTATION_MODEL, 40, 45)
            finally:
                archive.unlink(missing_ok=True)
            if not SEGMENTATION_MODEL.is_file():
                raise RuntimeError("分割模型解压后没有找到 model.onnx")
        embedding = embedding_path(model_id)
        if not (embedding.is_file() and _file_size(embedding) > 1_000_000):
            info = EMBEDDING_MODELS[model_id]
            try:
                _download(info["url"], embedding, 45, 100)
            except Exception:
                fallback = info.get("fallback_url")
                if not fallback:
                    raise
                with _lock:
                    _state.update(
                        {
                            "progress": 45,
                            "message": "官方下载较慢，正在切换镜像…",
                        }
                    )
                _download(fallback, embedding, 45, 100)
            if info.get("needs_metadata"):
                _ensure_wespeaker_metadata(embedding)
        with _lock:
            _state.update(
                {
                    "status": "ready",
                    "progress": 100,
                    "message": "说话人区分模型已就绪",
                    "finished_at": time.time(),
                }
            )
    except Exception as exc:
        with _lock:
            _state.update(
                {
                    "status": "error",
                    "message": "说话人区分模型下载失败",
                    "error": str(exc)[:500],
                    "finished_at": time.time(),
                }
            )


def _download(url: str, target: Path, start: int, end: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "Lan.Assistant/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        total = int(response.headers.get("Content-Length", "0") or 0)
        received = 0
        with partial.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                received += len(chunk)
                if total:
                    ratio = min(1.0, received / total)
                    progress = start + int((end - start) * ratio)
                    with _lock:
                        _state.update(
                            {
                                "progress": progress,
                                "message": f"正在下载 {target.name}（{received // (1024 * 1024)} MB）",
                            }
                        )
    partial.replace(target)


def _extract(archive: Path, target: Path) -> None:
    with tarfile.open(archive, "r:bz2") as tar:
        try:
            tar.extractall(target, filter="data")
        except TypeError:
            tar.extractall(target)


def _ensure_wespeaker_metadata(path: Path) -> None:
    """The Hugging Face mirror copy lacks sherpa-onnx metadata."""
    try:
        import onnx
        import onnxruntime as ort
    except Exception as exc:
        raise RuntimeError("声纹模型缺少 sherpa 元数据，且本机没有 onnx 修复依赖") from exc
    try:
        session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        if session.get_modelmeta().custom_metadata_map.get("framework"):
            return
    except Exception:
        pass
    model = onnx.load(str(path))
    existing = {prop.key for prop in model.metadata_props}
    fields = {
        "framework": "wespeaker",
        "language": "English",
        "url": "https://huggingface.co/chengdongliang/wespeaker",
        "comment": "Lan.Assistant speaker diarization embedding",
        "sample_rate": "16000",
        "output_dim": "256",
        "normalize_samples": "0",
    }
    for key, value in fields.items():
        if key not in existing:
            prop = model.metadata_props.add()
            prop.key = key
            prop.value = value
    onnx.save(model, str(path))


def warmup(model_id: str = DEFAULT_MODEL) -> bool:
    """Load models once so the first meeting doesn't pay the model-open cost."""
    if not is_ready(model_id):
        return False
    try:
        _make_diarizer(model_id)
        return True
    except Exception:
        return False


def _make_diarizer(model_id: str):
    import sherpa_onnx

    info = EMBEDDING_MODELS.get(model_id) or EMBEDDING_MODELS[DEFAULT_MODEL]
    seg_config = sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
        pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
            model=str(SEGMENTATION_MODEL)
        ),
        num_threads=max(1, min(4, (os.cpu_count() or 2))),
        provider="cpu",
    )
    embedding = str(embedding_path(model_id))
    config_obj = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=seg_config,
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=embedding,
            num_threads=max(1, min(4, (os.cpu_count() or 2))),
            provider="cpu",
        ),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=-1,
            threshold=0.35,
        ),
        min_duration_on=0.25,
        min_duration_off=0.5,
    )
    if not config_obj.validate():
        raise RuntimeError("说话人区分模型配置无效")
    return sherpa_onnx.OfflineSpeakerDiarization(config_obj), info


def diarize_turns(
    turns: list[dict[str, Any]],
    model_id: str = DEFAULT_MODEL,
    progress: Optional[Callable[[int, str], None]] = None,
) -> dict[str, Any]:
    """Cluster speaker embeddings for final turn PCM and map labels back.

    Each input item must have ``turn_id``, ``pcm`` (int16 numpy array) and
    ``started_at``.  A short silence gap is inserted between turns so the
    segmentation model sees natural boundaries.
    """
    import numpy as np

    if not turns:
        return {"ok": False, "error": "没有可分析的会议语音", "turns": 0}
    if not is_ready(model_id):
        return {"ok": False, "error": "说话人区分模型尚未下载", "turns": 0}
    diarizer, info = _make_diarizer(model_id)
    gap = np.zeros(int(16000 * 0.25), dtype=np.float32)
    pieces: list[np.ndarray] = []
    spans: list[tuple[str, float, float]] = []
    cursor = 0.0
    for index, item in enumerate(turns):
        pcm = np.asarray(item.get("pcm", []), dtype=np.int16)
        if pcm.size == 0:
            continue
        float_pcm = pcm.astype(np.float32) / 32768.0
        start = cursor
        end = start + len(float_pcm) / 16000.0
        pieces.append(float_pcm)
        pieces.append(gap)
        spans.append((str(item.get("turn_id", "")), start, end))
        cursor = end + len(gap) / 16000.0
        if progress and (index + 1) % 5 == 0:
            progress(int(10 + 60 * (index + 1) / max(1, len(turns))), "正在拼接会议音频…")
    if not pieces:
        return {"ok": False, "error": "会议语音为空", "turns": 0}
    if progress:
        progress(72, "正在做说话人分离…")
    audio = np.concatenate(pieces)
    result = diarizer.process(audio)
    segments = result.sort_by_start_time()
    if progress:
        progress(92, "正在对齐说话人和转录…")
    assignments: dict[str, str] = {}
    cluster_order: list[int] = []
    for segment in segments:
        if int(segment.speaker) not in cluster_order:
            cluster_order.append(int(segment.speaker))
    labels = {speaker: f"Speaker {index + 1}" for index, speaker in enumerate(cluster_order)}
    for turn_id, start, end in spans:
        overlap: dict[int, float] = {}
        for segment in segments:
            left = max(start, float(segment.start))
            right = min(end, float(segment.end))
            if right > left:
                speaker = int(segment.speaker)
                overlap[speaker] = overlap.get(speaker, 0.0) + (right - left)
        if overlap:
            best = max(overlap, key=overlap.get)
            assignments[turn_id] = labels.get(best, "Speaker 1")
        else:
            assignments[turn_id] = "Speaker 1"
    if progress:
        progress(100, "说话人区分完成")
    return {
        "ok": True,
        "assignments": assignments,
        "speakers": len(cluster_order),
        "segments": [
            {"start": float(s.start), "end": float(s.end), "speaker": int(s.speaker)}
            for s in segments
        ],
        "model": model_id,
    }
