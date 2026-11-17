from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any

import httpx


HEADERS = {
    "User-Agent": "MusicHistoryReelsStudio/2.0 (https://localhost; local-creator@example.com)"
}

_PT_STOPWORDS = frozenset({
    "que", "não", "nao", "com", "para", "você", "voce", "meu", "minha",
    "seu", "sua", "mais", "todos", "tudo", "tempo", "dias", "quando",
    "onde", "como", "ainda", "sempre", "mas", "por", "uma", "dos", "das",
    "nos", "nas", "ela", "ele", "esse", "essa", "isso", "mundo",
})

_NON_STUDIO_TAGS = ("live", "ao vivo", "remix", "acoustic", "acústico", "karaoke", "instrumental", "cover", "demo", "radio edit")


def parse_lrc(lrc_text: str, total_dur_sec: float | None = None) -> list[dict[str, Any]]:
    """
    Parse LRC `[mm:ss.xx]` text into chronologically sorted cues:
      [{"time": float, "end": float, "text": str}]
    Handles multi-timestamp LRC lines like `[00:17.05][01:30.20] Mother`.
    """
    if not lrc_text:
        return []

    tag_re = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
    raw_items: list[tuple[float, str]] = []

    for line in lrc_text.splitlines():
        matches = list(tag_re.finditer(line))
        if not matches:
            continue
        lyric = tag_re.sub("", line).strip()
        if not lyric or lyric.startswith("["):
            continue
        for m in matches:
            mins = int(m.group(1))
            secs = int(m.group(2))
            frac_str = m.group(3) or "0"
            frac = int(frac_str) / (1000.0 if len(frac_str) == 3 else 100.0)
            ts = round(mins * 60.0 + secs + frac, 2)
            raw_items.append((ts, lyric))

    raw_items.sort(key=lambda x: x[0])
    cues: list[dict[str, Any]] = []
    for i, (ts, text) in enumerate(raw_items):
        if i + 1 < len(raw_items):
            nxt = raw_items[i + 1][0]
            end_t = min(nxt, ts + 7.5)
        elif total_dur_sec and total_dur_sec > ts + 1.0:
            end_t = min(total_dur_sec - 0.5, ts + 5.5)
        else:
            end_t = ts + 4.5
        cues.append({"time": ts, "end": round(max(ts + 0.5, end_t), 2), "text": text})
    return cues


def _is_portuguese_text(text: str) -> bool:
    if not text:
        return True
    if any(ch in text.lower() for ch in ("ã", "õ", "ç", "á", "é", "í", "ó", "ú", "ê", "ô")):
        return True
    words = {re.sub(r"[^\w]", "", w.lower()) for w in text.split()}
    return len(words & _PT_STOPWORDS) >= 2


def translate_lines_pt_br(lines: list[str]) -> list[str]:
    """
    Free zero-key PT-BR lyric translator using public GTX endpoint with MyMemory fallback.
    Gracefully skips lines already in Portuguese or returns original text if offline.
    """
    if not lines:
        return []
    joined = " ".join(lines)
    if _is_portuguese_text(joined):
        return [ln.strip() for ln in lines]

    results = [ln.strip() for ln in lines]
    try:
        with httpx.Client(timeout=6.0, headers=HEADERS, follow_redirects=True) as client:
            # Batch lines cleanly separated by newline
            payload = "\n".join(results)
            r = client.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": "auto", "tl": "pt", "dt": "t", "q": payload},
            )
            if r.status_code == 200:
                chunks = r.json()[0] or []
                full_pt = "".join(str(c[0] or "") for c in chunks if c)
                split_pt = [s.strip() for s in full_pt.splitlines() if s.strip()]
                if len(split_pt) == len(results):
                    return split_pt
    except Exception:
        pass

    # Fallback: per-line MyMemory
    translated: list[str] = []
    try:
        with httpx.Client(timeout=5.0, headers=HEADERS) as client:
            for ln in results:
                if not ln or _is_portuguese_text(ln):
                    translated.append(ln)
                    continue
                r2 = client.get(
                    "https://api.mymemory.translated.net/get",
                    params={"q": ln, "langpair": "en|pt-BR"},
                )
                if r2.status_code == 200:
                    txt = str((r2.json().get("responseData") or {}).get("translatedText") or "").strip()
                    translated.append(txt or ln)
                else:
                    translated.append(ln)
        return translated
    except Exception:
        return results


def fetch_synced_lyrics(
    song_title: str,
    artist: str,
    target_duration_sec: float | None = None,
    local_audio_path: Path | None = None,
) -> dict[str, Any]:
    """
    Fetch time-synced LRC lyrics completely Zero-API:
      1. Local sibling `.lrc` next to `local_audio_path` (if present)
      2. LRCLIB (`https://lrclib.net/api/search` + `/api/get`) matching closest studio duration.
    """
    if local_audio_path and local_audio_path.exists():
        lrc_sidecar = local_audio_path.with_suffix(".lrc")
        if lrc_sidecar.exists():
            cues = parse_lrc(lrc_sidecar.read_text(encoding="utf-8", errors="ignore"), target_duration_sec)
            if cues:
                return {
                    "source": f"local_lrc:{lrc_sidecar.name}",
                    "duration": float(target_duration_sec or cues[-1]["end"]),
                    "cues": cues,
                    "plain_lyrics": "\n".join(c["text"] for c in cues),
                }

    artist_low = artist.lower().strip()
    song_low = song_title.lower().strip()
    want_live = any(t in song_low or t in artist_low for t in ("live", "ao vivo", "acoustic", "unplugged"))

    candidates: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=8.0, headers=HEADERS, follow_redirects=True) as client:
            r_get = client.get(
                "https://lrclib.net/api/get",
                params={"track_name": song_title, "artist_name": artist},
            )
            if r_get.status_code == 200 and isinstance(r_get.json(), dict):
                g = dict(r_get.json())
                g["_from_get"] = True
                candidates.append(g)

            # Query search when target_duration_sec is given (matching exact local file cut) OR canonical get lacked syncedLyrics
            if target_duration_sec is not None or not any((c.get("syncedLyrics") or "").strip() for c in candidates):
                r_search = client.get(
                    "https://lrclib.net/api/search",
                    params={"track_name": song_title, "artist_name": artist},
                )
                if r_search.status_code == 200 and isinstance(r_search.json(), list):
                    candidates.extend(r_search.json())

                if len(candidates) <= 1:
                    r_q = client.get(
                        "https://lrclib.net/api/search",
                        params={"q": f"{artist} {song_title}"},
                    )
                    if r_q.status_code == 200 and isinstance(r_q.json(), list):
                        candidates.extend(r_q.json())
    except Exception:
        return {"source": "none", "duration": float(target_duration_sec or 0.0), "cues": [], "plain_lyrics": ""}

    if not candidates:
        return {"source": "none", "duration": float(target_duration_sec or 0.0), "cues": [], "plain_lyrics": ""}

    def score_candidate(item: dict[str, Any]) -> tuple[int, float]:
        has_sync = 1 if (item.get("syncedLyrics") or "").strip() else 0
        ia = str(item.get("artistName") or "").lower()
        it = str(item.get("trackName") or "").lower()
        exact_artist = 1 if (ia == artist_low or artist_low in ia or ia in artist_low) else 0
        exact_song = 1 if (it == song_low or song_low in it) else 0
        non_studio_pen = 0
        if not want_live and any(tag in it or tag in str(item.get("albumName") or "").lower() for tag in _NON_STUDIO_TAGS):
            non_studio_pen = -3

        dur = float(item.get("duration") or 0.0)
        if target_duration_sec and dur > 10.0:
            dur_diff = abs(dur - float(target_duration_sec))
        elif 90.0 <= dur <= 360.0:
            dur_diff = 0.0
        else:
            dur_diff = 120.0

        priority = has_sync * 10 + exact_artist * 5 + exact_song * 4 + non_studio_pen
        return (priority, -dur_diff)

    best = max(candidates, key=score_candidate)
    best_dur = float(best.get("duration") or target_duration_sec or 0.0)
    synced = str(best.get("syncedLyrics") or "").strip()
    plain = str(best.get("plainLyrics") or "").strip()
    cues = parse_lrc(synced, total_dur_sec=best_dur)

    if cues:
        return {
            "source": "lrclib",
            "track_name": str(best.get("trackName") or song_title),
            "artist_name": str(best.get("artistName") or artist),
            "duration": best_dur,
            "cues": cues,
            "plain_lyrics": plain or "\n".join(c["text"] for c in cues),
        }

    return {
        "source": "lrclib_plain" if plain else "none",
        "duration": best_dur,
        "cues": [],
        "plain_lyrics": plain,
    }


def _merge_hook_window(cues: list[dict[str, Any]], start_idx: int, min_dur: float = 2.6, max_dur: float = 4.6) -> dict[str, Any]:
    """
    Combine consecutive short LRC phrases into a cohesive sung hook window.
    For example: `[17.05] Mother` + `[19.09] Tell your children not to walk my way` -> ~4.5s combined break!
    """
    c0 = cues[start_idx]
    t0 = float(c0["time"])
    texts = [str(c0["text"]).strip()]
    t_end = float(c0["end"])
    j = start_idx

    while j + 1 < len(cues):
        nxt = cues[j + 1]
        cur_span = float(nxt["end"]) - t0
        cur_words = sum(len(t.split()) for t in texts)
        if (t_end - t0 < min_dur or cur_words <= 4) and (float(nxt["time"]) - t0) < max_dur - 0.8 and cur_span <= max_dur + 0.6:
            j += 1
            texts.append(str(nxt["text"]).strip())
            t_end = float(nxt["end"])
            if cur_span >= min_dur + 0.4:
                break
        else:
            break

    raw_dur = min(max_dur, max(2.4, t_end - t0))
    # Back-off ~0.12s before timestamp tag so initial vocal onset/breath is never clipped
    cue_audio_start = round(max(0.0, t0 - 0.12), 2)
    return {
        "start_sec": cue_audio_start,
        "lyric_raw_time": round(t0, 2),
        "duration_sec": round(raw_dur + 0.12, 2),
        "lyric_en": " / ".join(texts),
        "end_idx": j,
    }


def select_lyric_break_cues(
    cues: list[dict[str, Any]],
    song_title: str,
    track_dur: float | None = None,
    n_breaks: int = 4,
    finale_sec: float = 12.0,
) -> tuple[list[dict[str, Any]], float | None]:
    """
    Choose contrasting, recognizable lyric hook cues spaced naturally through the song,
    translate non-Portuguese lyric lines into bracketed PT-BR `(tradução...)`,
    and pick a high-energy finale climax timestamp.
    """
    if not cues:
        return [], None

    dur = float(track_dur or (cues[-1]["end"] + 15.0))
    title_toks = {w.lower() for w in re.split(r"\W+", song_title) if len(w) >= 3}

    valid_idxs = [
        i for i, c in enumerate(cues)
        if len(re.sub(r"[\W_]+", "", str(c["text"]))) >= 2 and float(c["time"]) < max(10.0, dur - 8.0)
    ]
    if not valid_idxs:
        return [], None

    windows = []
    last_t = -999.0
    for idx in valid_idxs:
        t_cur = float(cues[idx]["time"])
        if t_cur - last_t < 7.0:
            continue
        win = _merge_hook_window(cues, idx, min_dur=2.6, max_dur=5.6 if not windows else 4.1)
        txt = win["lyric_en"]
        txt_low = txt.lower()
        has_title = any(tok in txt_low for tok in title_toks)
        n_words = len(txt.split())
        win["has_title"] = has_title
        win["norm_key"] = re.sub(r"[^a-z0-9à-ÿ]+", "", txt_low)[:28]
        # Dramatic punchlines (questions, exclamations, rich phrases) score naturally high
        drama = (0.8 if "?" in txt or "!" in txt else 0.0) + min(2.2, n_words * 0.22)
        win["base_score"] = drama
        windows.append(win)
        last_t = float(cues[win["end_idx"]]["time"])

    if not windows:
        return [], None

    picked: list[dict[str, Any]] = []
    slot_fracs = [0.07, 0.23, 0.36, 0.56][:n_breaks]
    used_starts: list[float] = []
    used_keys: set[str] = set()

    for slot_i, frac in enumerate(slot_fracs):
        if slot_i == 0:
            # Break 1: anchor right onto the very first iconic vocal line!
            first_win = windows[0]
            picked.append(first_win)
            used_starts.append(float(first_win["start_sec"]))
            used_keys.add(str(first_win["norm_key"]))
            continue

        target_t = dur * frac if dur > 60 else (slot_i * max(5.0, (dur - 12.0) / max(1, n_breaks)))
        best_win = None
        best_val = -1e9
        for w in windows:
            st = float(w["start_sec"])
            nkey = str(w["norm_key"])
            if st <= used_starts[-1] + 7.5:
                continue
            if nkey in used_keys:
                continue
            if st > max(12.0, dur - max(16.0, finale_sec + 4.0)):
                continue

            # Reward diversity (avoid repeating the exact same title chorus in middle breaks)
            title_adj = (1.2 if w["has_title"] else 0.0) if slot_i == len(slot_fracs) - 1 else (-0.4 if w["has_title"] else 0.8)
            dist_pen = abs(st - target_t) * 0.07
            val = float(w["base_score"]) + title_adj - dist_pen
            if val > best_val:
                best_val = val
                best_win = w

        if best_win is not None:
            picked.append(best_win)
            used_starts.append(float(best_win["start_sec"]))
            used_keys.add(str(best_win["norm_key"]))

    picked.sort(key=lambda x: float(x["start_sec"]))

    en_lines = [p["lyric_en"] for p in picked]
    pt_lines = translate_lines_pt_br(en_lines)
    for p, pt in zip(picked, pt_lines):
        pt_clean = pt.strip()
        if pt_clean and not pt_clean.startswith("("):
            p["lyric_pt"] = f"({pt_clean})"
        else:
            p["lyric_pt"] = pt_clean

    finale_start = round(max(0.0, min(dur - max(10.0, finale_sec) - 2.0, dur * 0.68)), 2) if dur > finale_sec + 12.0 else None
    if picked and finale_start is not None and abs(finale_start - picked[-1]["start_sec"]) < 8.0:
        cand_fin = picked[-1]["start_sec"] + picked[-1]["duration_sec"] + 6.0
        if cand_fin + finale_sec < dur - 2.0:
            finale_start = round(cand_fin, 2)

    return picked, finale_start
