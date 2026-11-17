from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field


class TopicTrendItem(BaseModel):
    id: str
    title: str
    source: str = ""
    feed_type: str = "news"
    published_at: str = ""
    summary: str = ""
    url: str = ""
    trend_score: int = 50
    confidence: str = "Notícia / Imprensa"
    suggested_angle: str = ""
    already_covered: bool = False


class ShortReferenceAnalysis(BaseModel):
    source_url: str
    video_id: str | None = None
    title: str = ""
    channel: str = ""
    hook_style: str = ""
    narrative_structure: str = ""
    visual_style: str = ""
    subtitle_style: str = ""
    pacing_notes: str = ""
    extracted_facts: list[str] = Field(default_factory=list)
    transcript: str = ""
    suggested_prompt_guidance: str = ""
    analysis_method: str = ""


class WordTiming(BaseModel):
    word: str
    start: float
    end: float


class SceneItem(BaseModel):
    index: int
    title: str = ""
    narration_text: str
    visual_prompt: str = ""
    search_query: str = ""
    video_query: str = ""
    shot_intent: str = ""
    asset_type: Literal["auto", "image", "video", "html"] = "auto"
    effect: Literal[
        "zoom_in",
        "zoom_out",
        "pan_left",
        "pan_right",
        "ken_burns_duotone",
        "vintage_grain",
    ] = "zoom_in"
    highlight_words: list[str] = Field(default_factory=list)
    image_path: str | None = None
    video_path: str | None = None
    video_start_sec: float = 0.0
    media_id: str | None = None
    media_source: str = ""
    framing_mode: Literal["crop_center", "crop_focus", "blur_fill"] = "crop_center"
    focus_x: float = 0.5
    html_path: str | None = None
    candidate_urls: list[str] = Field(default_factory=list)
    candidate_items: list[dict[str, Any]] = Field(default_factory=list)
    music_break_after_sec: float = 0.0
    music_break_audio_start_sec: float | None = None
    music_break_lyric_en: str = ""
    music_break_lyric_pt: str = ""
    quote_text: str = ""
    duration_sec: float | None = None
    visual_layout: Literal[
        "auto",
        "pip_card",
        "split_top_image",
        "broll_cutaway",
        "full_media",
    ] = "auto"
    overlay_image_path: str | None = None
    overlay_label: str = ""
    overlay_pos: Literal["top_center", "top_right", "top_left", "center"] = "top_center"
    overlay_scale: float = 0.56
    overlay_start_sec: float = 0.4
    overlay_duration_sec: float = 0.0
    mockup_style: Literal[
        "auto",
        "smartphone_reels",
        "news_portal",
        "social_post",
        "hud_radar",
        "floating_glass",
        "none",
    ] = "auto"
    motion_preset: Literal[
        "auto",
        "spring_pop_tilt",
        "whip_pan_card",
        "kinetic_slam",
        "float_3d_orbit",
        "basic_fade",
    ] = "auto"
    ai_image_prompt: str = ""
    overlay_mode: Literal[
        "auto",
        "matched_photo",
        "ai_generated",
        "none_motion_only",
        "manual",
    ] = "auto"
    overlay_reason: str = ""


class ShortsProject(BaseModel):
    id: str
    niche: Literal["music_history", "gaming_news"] = "gaming_news"
    # Core GTA 6 fields
    title: str = ""
    game: str = "GTA 6"
    topic_angle: str = ""
    topic_source_url: str = ""
    fact_confidence: str = "Notícia / Imprensa"
    max_duration_sec: float = 58.0
    speech_rate: str = "+14%"
    viral_archetype: str = "auto"
    hook_title: str = ""
    narration_full: str = ""
    scenes: list[SceneItem] = Field(default_factory=list)
    youtube_title: str = ""
    youtube_description: str = ""
    youtube_tags: list[str] = Field(default_factory=list)
    tiktok_caption: str = ""
    instagram_caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    accent_color: str = "#00F0FF"
    style_preset: Literal[
        "vice_city_neon",
        "documental_rock",
        "viral_pop_historia",
        "zine_punk_underground",
        "vinil_minimal_audio",
    ] = "vice_city_neon"
    subtitle_style: Literal["vice_city", "viral_pop", "clean_bold", "gold_essay"] = "vice_city"
    tts_engine: Literal["edge_tts", "gemini_tts"] = "edge_tts"
    voice_name: str = "pt-BR-AntonioNeural"
    visual_engine: Literal[
        "auto_free_media",
        "auto_hybrid",
        "gemini_image",
        "wikimedia_archive",
        "archive_org_video",
        "procedural_card",
        "custom_uploads",
    ] = "auto_free_media"
    audio_file: str | None = None
    audio_clip_start: float | None = None
    audio_clip_duration: float = 58.0
    reference_shorts: list[str] = Field(default_factory=list)
    reference_notes: str = ""
    research_summary: str = ""
    news_media_manifest: dict[str, Any] = Field(default_factory=dict)
    thumbnail_style: Literal[
        "auto",
        "vice_city_magazine",
        "breaking_alert",
        "split_versus",
        "editorial_music",
    ] = "vice_city_magazine"
    thumbnail_headline: str = ""
    thumbnail_badge: str = ""
    thumbnail_scene_index: int | None = None
    outputs: dict[str, str] = Field(default_factory=dict)
    qa_report: dict[str, Any] = Field(default_factory=dict)
    used_media: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "draft"
    is_posted: bool = False
    posted_at: str | None = None
    posted_platform: str = "youtube"
    posted_url: str = ""
    posted_notes: str = ""
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Legacy fields with defaults for backward-compatibility with older projects
    song_title: str = "GTA 6"
    artist: str = "Rockstar Games"
    year: str = ""
    genre: str = "Games / GTA 6"
    video_mode: Literal["single_all_in_one", "two_reels_combo"] = "single_all_in_one"
    opening_mode: Literal["lyric_hook", "music_intro"] = "lyric_hook"
    overlay_style: Literal["clean_reels", "banner_top"] = "clean_reels"
    intro_music_sec: float = 0.0
    music_break_sec: float = 0.0
    finale_music_sec: float = 0.25
    finale_audio_start_sec: float | None = None
    lyrics_cues: list[dict[str, Any]] = Field(default_factory=list)
    band_video_clips: list[str] = Field(default_factory=list)
    reel2_renderer: Literal["html_canvas", "ffmpeg_numpy"] = "html_canvas"
    html_hook_card: bool = False
    beat_sync: bool = False
    audio_export_mode: Literal["both", "with_music", "clean_for_instagram"] = "both"
    reel2_title: str = ""
    reel2_subtitle: str = ""
    reel2_visual_style: Literal["embers_emblem", "vinyl_pulse", "neon_wave"] = "neon_wave"
    reel2_center_image: str | None = None


# Alias for backward-compatibility across all modules
MusicStoryProject = ShortsProject


class CreateProjectRequest(BaseModel):
    title: str = ""
    game: str = "GTA 6"
    # Legacy alias fields
    song_title: str = ""
    artist: str = ""
    year: str = ""
    genre: str = "Games / GTA 6"
    topic_angle: str = ""
    topic_source_url: str = ""
    fact_confidence: str = "Notícia / Imprensa"
    max_duration_sec: float = 58.0
    speech_rate: str = "+14%"
    viral_archetype: str = "auto"
    niche: Literal["music_history", "gaming_news"] = "gaming_news"
    reference_shorts: list[str] = Field(default_factory=list)
    reference_notes: str = ""
    video_mode: Literal["single_all_in_one", "two_reels_combo"] = "single_all_in_one"
    opening_mode: Literal["lyric_hook", "music_intro"] = "lyric_hook"
    overlay_style: Literal["clean_reels", "banner_top"] = "clean_reels"
    intro_music_sec: float = 0.0
    music_break_sec: float = 0.0
    finale_music_sec: float = 0.25
    script_engine: Literal["free_research", "gemini_with_research", "offline_template"] = "free_research"
    use_gemini_research: bool | None = None
    style_preset: Literal[
        "vice_city_neon",
        "documental_rock",
        "viral_pop_historia",
        "zine_punk_underground",
        "vinil_minimal_audio",
    ] = "vice_city_neon"
    tts_engine: Literal["edge_tts", "gemini_tts"] = "edge_tts"
    voice_name: str = "pt-BR-AntonioNeural"
    visual_engine: Literal[
        "auto_free_media",
        "auto_hybrid",
        "gemini_image",
        "wikimedia_archive",
        "archive_org_video",
        "procedural_card",
        "custom_uploads",
    ] = "auto_free_media"
    reel2_renderer: Literal["html_canvas", "ffmpeg_numpy"] = "html_canvas"
    html_hook_card: bool = False
    beat_sync: bool = False
    accent_color: str = "#00F0FF"
    subtitle_style: Literal["vice_city", "viral_pop", "clean_bold", "gold_essay"] = "vice_city"
    reel2_visual_style: Literal["embers_emblem", "vinyl_pulse", "neon_wave"] = "neon_wave"
    audio_export_mode: Literal["both", "with_music", "clean_for_instagram"] = "both"
    audio_clip_duration: float = 58.0


class AnalyzeShortsRequest(BaseModel):
    urls: list[str]
    use_gemini: bool = False
