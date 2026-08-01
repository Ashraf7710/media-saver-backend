import yt_dlp
import re
import os
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

class MediaExtractor:
    QUALITY_ORDER = {
        "4320p": 1, "2160p": 2, "1440p": 3, "1080p": 4,
        "720p": 5, "480p": 6, "360p": 7, "240p": 8, "144p": 9
    }

    def __init__(self):
        self.cookies_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "cookies.txt"
        )

        self.base_opts = {
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "ignoreerrors": False,
            "socket_timeout": 15,
            "retries": 2,
            "skip_download": True,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
            },
        }

    def get_version(self) -> str:
        try:
            return yt_dlp.version.__version__
        except Exception:
            return "unknown"

    def get_supported_platforms(self) -> List[Dict]:
        return [
            {"name": "YouTube",      "domains": ["youtube.com", "youtu.be"]},
            {"name": "Instagram",    "domains": ["instagram.com"]},
            {"name": "TikTok",       "domains": ["tiktok.com"]},
            {"name": "Twitter/X",    "domains": ["twitter.com", "x.com"]},
            {"name": "Facebook",     "domains": ["facebook.com"]},
            {"name": "و 1800+ موقع", "domains": []},
        ]

    def extract(self, url: str, preferred_quality: str = "best",
                start_time: str = None, end_time: str = None) -> Dict:
        url = self._clean_url(url)
        platform = self._detect_platform(url)

        try:
            opts = self._get_platform_opts(platform, use_cookies=True)

            if start_time or end_time:
                opts["download_ranges"] = self._build_ranges(start_time, end_time)
                opts["force_keyframes_at_cuts"] = True

            return self._do_extract(url, opts, platform)
        except Exception as e:
            error_msg = str(e).lower()
            logger.warning(f"First attempt failed: {error_msg[:200]}")

            if platform == "youtube":
                try:
                    logger.info("Retrying without cookies...")
                    opts = self._get_platform_opts(platform, use_cookies=False)
                    if start_time or end_time:
                        opts["download_ranges"] = self._build_ranges(start_time, end_time)
                        opts["force_keyframes_at_cuts"] = True
                    return self._do_extract(url, opts, platform)
                except Exception as e2:
                    raise Exception(self._translate_error(str(e2)))

            raise Exception(self._translate_error(str(e)))

    def _do_extract(self, url: str, opts: Dict, platform: str) -> Dict:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise Exception("لم يتم العثور على محتوى")

        if info.get("_type") == "playlist":
            entries = info.get("entries", [])
            if entries:
                info = entries[0]
            else:
                raise Exception("القائمة فارغة")

        return self._build_result(info, platform)

    def _clean_url(self, url: str) -> str:
        url = url.strip()

        if "youtube.com/results?" in url or "youtube.com/search" in url:
            raise Exception("الرجاء إدخال رابط فيديو مباشر وليس رابط نتائج بحث")

        url = re.sub(
            r'youtube\.com/shorts/([a-zA-Z0-9_-]+)',
            r'youtube.com/watch?v=\1', url
        )
        url = re.sub(r'\?igsh=[^&]*', '', url)
        url = re.sub(r'[?&](utm_[^&]*|si=[^&]*|feature=[^&]*)', '', url)
        return url

    def _detect_platform(self, url: str) -> str:
        url_lower = url.lower()
        patterns = {
            "youtube":     ["youtube.com", "youtu.be"],
            "instagram":   ["instagram.com"],
            "tiktok":      ["tiktok.com"],
            "twitter":     ["twitter.com", "x.com"],
            "facebook":    ["facebook.com", "fb.watch"],
            "snapchat":    ["snapchat.com"],
            "reddit":      ["reddit.com"],
            "pinterest":   ["pinterest.com"],
            "vimeo":       ["vimeo.com"],
            "dailymotion": ["dailymotion.com"],
            "soundcloud":  ["soundcloud.com"],
            "twitch":      ["twitch.tv"],
        }
        for platform, domains in patterns.items():
            if any(d in url_lower for d in domains):
                return platform
        return "other"

    def _get_platform_opts(self, platform: str, use_cookies: bool = True) -> Dict:
        opts = {**self.base_opts}

        if use_cookies and os.path.exists(self.cookies_path):
            opts["cookiefile"] = self.cookies_path

        opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

        if platform == "youtube":
            # ✅ إعدادات YouTube - كل الجودات
            opts["extractor_args"] = {
                "youtube": {
                    "player_client": ["mweb", "tv_simply", "tv_embedded"],
                    "player_skip": ["configs", "webpage"],
                    "formats": "missing_pot",
                }
            }
            proxy_url = os.environ.get("YOUTUBE_PROXY")
            if proxy_url:
                opts["proxy"] = proxy_url

        elif platform == "instagram":
            opts["http_headers"] = {
                **self.base_opts["http_headers"],
                "X-IG-App-ID": "936619743392459",
            }
            # ✅ دعم الصور والفيديوهات المتعددة
            opts["format"] = "best"
            # ✅ استخراج كل عناصر الـ Carousel
            opts["extract_flat"] = False
        elif platform == "tiktok":
            opts["format"] = "best[format_id*=nowatermark]/best[vcodec^=avc]/best[ext=mp4]/best"

        return opts

    def _build_result(self, info: Dict, platform: str) -> Dict:
        title = info.get("title", "Unknown")
        thumbnail = info.get("thumbnail", "")
        duration = info.get("duration", 0)
        uploader = info.get("uploader", "")
        description = info.get("description", "")

        qualities = self._extract_qualities(info)
        audio_only = self._extract_audio(info)
        subtitles = self._extract_subtitles(info)
        direct_url = info.get("url")

        # ✅ معالجة Instagram - صور مفردة، Carousel، فيديوهات
        if platform == "instagram":
            # فحص إذا كان Carousel (صور/فيديوهات متعددة)
            entries = info.get("entries", [])

            if entries and len(entries) > 0:
                # Carousel - عدة عناصر
                qualities = []
                for idx, entry in enumerate(entries, 1):
                    entry_url = entry.get("url") or entry.get("webpage_url")
                    if not entry_url:
                        # جرب formats
                        entry_formats = entry.get("formats", [])
                        if entry_formats:
                            entry_url = entry_formats[-1].get("url")

                    if entry_url:
                        is_video = entry.get("vcodec", "none") != "none" or entry.get("ext") in ("mp4", "webm")
                        ext = entry.get("ext", "jpg" if not is_video else "mp4")

                        qualities.append({
                            "quality": f"عنصر {idx}",
                            "url": entry_url,
                            "format": ext,
                            "filesize": entry.get("filesize") or entry.get("filesize_approx") or 0,
                            "has_audio": is_video,
                            "vcodec": entry.get("vcodec", "none"),
                            "acodec": entry.get("acodec", "none"),
                            "fps": entry.get("fps"),
                            "height": entry.get("height", 0),
                            "width": entry.get("width", 0),
                            "media_type": "video" if is_video else "image",
                        })

                # إضافة خيار تحميل الكل
                if len(qualities) > 1:
                    qualities.insert(0, {
                        "quality": f"📦 تحميل الكل ({len(qualities)} عنصر)",
                        "url": "MULTI_DOWNLOAD",
                        "format": "mixed",
                        "filesize": sum(q.get("filesize", 0) for q in qualities),
                        "has_audio": True,
                        "vcodec": "mixed",
                        "acodec": "mixed",
                        "media_type": "multi",
                    })

            elif direct_url:
                # صورة أو فيديو مفرد
                is_video = info.get("vcodec", "none") != "none" or info.get("ext") in ("mp4", "webm")
                ext = info.get("ext", "jpg" if not is_video else "mp4")
                height = info.get("height", 0)
                width = info.get("width", 0)

                quality_label = f"{height}p" if height and is_video else ("📷 صورة" if not is_video else "HD")

                filesize = info.get("filesize") or info.get("filesize_approx") or 0
                if not filesize and duration and is_video:
                    tbr = info.get("tbr") or 0
                    if tbr:
                        filesize = int((tbr * 1000 / 8) * duration)

                qualities = [{
                    "quality": quality_label,
                    "url": direct_url,
                    "format": ext,
                    "filesize": filesize,
                    "has_audio": is_video,
                    "vcodec": info.get("vcodec", "h264" if is_video else "none"),
                    "acodec": info.get("acodec", "aac" if is_video else "none"),
                    "fps": info.get("fps"),
                    "height": height,
                    "width": width,
                    "media_type": "video" if is_video else "image",
                }]

        elif direct_url and platform in ("tiktok", "facebook", "twitter"):
            has_audio = info.get("acodec", "none") != "none"
            height = info.get("height", 0)
            width = info.get("width", 0)

            quality_label = f"{height}p" if height else "HD"

            filesize = info.get("filesize") or info.get("filesize_approx") or 0
            if not filesize and duration:
                tbr = info.get("tbr") or 0
                if tbr:
                    filesize = int((tbr * 1000 / 8) * duration)

            qualities = [{
                "quality": quality_label,
                "url": direct_url,
                "format": info.get("ext", "mp4"),
                "filesize": filesize,
                "has_audio": True,
                "vcodec": info.get("vcodec", "h264"),
                "acodec": info.get("acodec", "aac"),
                "fps": info.get("fps"),
                "height": height,
                "width": width,
            }]

        if not qualities:
            direct_url = info.get("url")
            if direct_url:
                qualities = [{
                    "quality": "Default",
                    "url": direct_url,
                    "format": info.get("ext", "mp4"),
                    "filesize": info.get("filesize", 0),
                    "has_audio": True,
                    "vcodec": info.get("vcodec", "unknown"),
                    "acodec": info.get("acodec", "unknown"),
                    "fps": info.get("fps"),
                    "height": info.get("height", 0),
                    "width": info.get("width", 0),
                }]

        if not qualities:
            raise Exception("لم يتم العثور على روابط تحميل")

        qualities.sort(key=lambda q: self.QUALITY_ORDER.get(q["quality"], 99))

        return {
            "title": self._clean_title(title),
            "thumbnail": thumbnail,
            "duration": duration,
            "platform": platform.capitalize(),
            "uploader": uploader,
            "description": description[:500] if description else "",
            "qualities": qualities,
            "audio_only": audio_only,
            "subtitles": subtitles,
        }

    def _extract_qualities(self, info: Dict) -> List[Dict]:
        formats = info.get("formats", [])
        if not formats:
            return []

        duration = info.get("duration", 0)
        qualities = []
        seen = set()
        best_audio = self._find_best_audio(formats)

        for f in formats:
            try:
                url = f.get("url")
                if not url:
                    continue

                vcodec = f.get("vcodec") or "none"
                acodec = f.get("acodec") or "none"
                ext = f.get("ext") or "mp4"
                height = f.get("height")

                if ext in ("mhtml", "html"):
                    continue

                if vcodec == "none" and acodec != "none":
                    continue

                is_video_format = (
                    vcodec != "none" or
                    ext in ("mp4", "webm", "mov", "mkv", "flv", "3gp") or
                    f.get("width") or
                    f.get("height")
                )
                if not is_video_format:
                    continue

                if not height or height < 100:
                    continue

                quality_label = f"{height}p"
                standard_heights = [144, 240, 360, 480, 720, 1080, 1440, 2160, 4320]
                closest_height = min(standard_heights, key=lambda x: abs(x - height))
                if abs(closest_height - height) > 100:
                    continue
                quality_label = f"{closest_height}p"

                has_audio = acodec != "none"
                key = f"{quality_label}_{has_audio}_{ext}"

                if key in seen:
                    continue
                seen.add(key)

                filesize = f.get("filesize") or f.get("filesize_approx") or 0
                if not filesize and duration:
                    tbr = f.get("tbr") or f.get("vbr") or 0
                    if tbr:
                        filesize = int((tbr * 1000 / 8) * duration)

                q = {
                    "quality": quality_label,
                    "url": url,
                    "format": ext,
                    "filesize": filesize,
                    "has_audio": has_audio,
                    "vcodec": self._simplify_codec(vcodec),
                    "acodec": self._simplify_codec(acodec) if has_audio else "none",
                    "fps": f.get("fps"),
                    "height": height or 0,
                    "width": f.get("width"),
                }

                if not has_audio and best_audio:
                    audio_size = best_audio.get("filesize") or best_audio.get("filesize_approx") or 0
                    if not audio_size and duration:
                        abr = best_audio.get("abr") or best_audio.get("tbr") or 0
                        if abr:
                            audio_size = int((abr * 1000 / 8) * duration)

                    q["audio_url"] = best_audio["url"]
                    q["audio_format"] = best_audio.get("ext", "m4a")
                    q["audio_filesize"] = audio_size

                qualities.append(q)
            except Exception as ex:
                logger.warning(f"Skipping format due to error: {ex}")
                continue

        def codec_priority(q):
            v = q.get("vcodec", "").lower()
            if "h.264" in v or "avc" in v or "h264" in v:
                return 0
            if "h.265" in v or "hevc" in v:
                return 1
            if "vp9" in v or "vp09" in v:
                return 2
            return 3

        qualities.sort(key=codec_priority)

        final = []
        seen_h = set()
        merged = [q for q in qualities if q["has_audio"]]
        vo_only = [q for q in qualities if not q["has_audio"]]

        for q in merged:
            if q["quality"] not in seen_h:
                seen_h.add(q["quality"])
                final.append(q)

        for q in vo_only:
            if q["quality"] not in seen_h:
                seen_h.add(q["quality"])
                final.append(q)

        return final

    def _extract_audio(self, info: Dict) -> List[Dict]:
        formats = info.get("formats", [])
        duration = info.get("duration", 0)
        result = []
        seen = set()

        for f in formats:
            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")
            url = f.get("url")
            ext = f.get("ext", "")

            if vcodec != "none" or acodec == "none" or not url:
                continue

            if ext in ("mp4", "webm", "mov", "mkv"):
                continue

            abr = f.get("abr") or f.get("tbr") or 0
            ext = f.get("ext", "m4a")

            if abr >= 256:
                label = "High (256kbps)"
            elif abr >= 128:
                label = "Medium (128kbps)"
            elif abr >= 64:
                label = "Low (64kbps)"
            else:
                label = f"Audio ({int(abr)}kbps)" if abr else "Audio"

            key = f"{label}_{ext}"
            if key in seen:
                continue
            seen.add(key)

            filesize = f.get("filesize") or f.get("filesize_approx") or 0
            if not filesize and duration and abr:
                filesize = int((abr * 1000 / 8) * duration)

            result.append({
                "quality": label,
                "url": url,
                "format": ext,
                "filesize": filesize,
                "abr": abr,
                "acodec": self._simplify_codec(acodec),
            })

        result.sort(key=lambda x: x.get("abr", 0), reverse=True)
        return result[:5]

    def _find_best_audio(self, formats: List[Dict]) -> Optional[Dict]:
        audio = [
            f for f in formats
            if f.get("vcodec", "none") == "none"
            and f.get("acodec", "none") != "none"
            and f.get("url")
        ]
        if not audio:
            return None
        audio.sort(key=lambda x: x.get("abr") or x.get("tbr") or 0, reverse=True)
        for f in audio:
            if f.get("ext") in ("m4a", "mp4"):
                return f
        return audio[0]

    def _simplify_codec(self, codec: str) -> str:
        if not codec or codec == "none":
            return "none"
        codec = codec.lower().split(".")[0]
        return {
            "avc1": "H.264", "h264": "H.264",
            "hev1": "H.265", "hevc": "H.265",
            "vp9": "VP9", "vp8": "VP8",
            "av01": "AV1", "mp4a": "AAC",
            "opus": "Opus", "vorbis": "Vorbis",
        }.get(codec, codec.upper())

    def _clean_title(self, title: str) -> str:
        title = re.sub(r'[<>:"/\\|?*]', '', title)
        title = re.sub(r'\s+', ' ', title).strip()
        return title[:200]

    def _build_ranges(self, start_time, end_time):
        def time_to_seconds(t):
            if not t:
                return None
            parts = str(t).split(":")
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            return float(t)

        start = time_to_seconds(start_time) or 0
        end = time_to_seconds(end_time)

        def ranges_func(info_dict, ydl):
            return [{
                "start_time": start,
                "end_time": end,
                "title": "clip"
            }]

        return ranges_func

    def _translate_error(self, error: str) -> str:
        error_lower = error.lower()
        translations = {
            "video unavailable": "الفيديو غير متاح",
            "private video": "هذا فيديو خاص",
            "sign in": "يتطلب تسجيل الدخول",
            "age-restricted": "محتوى مقيد بالعمر",
            "copyright": "تم إزالته بسبب حقوق النشر",
            "not found": "لم يتم العثور على المحتوى",
            "geo restricted": "غير متاح في منطقتك",
            "live stream": "البث المباشر غير مدعوم",
            "unsupported url": "هذا رابط غير مدعوم",
        }
        for key, translation in translations.items():
            if key in error_lower:
                return translation
        return f"خطأ: {error[:200]}"

    def _extract_subtitles(self, info: Dict) -> List[Dict]:
        result = []
        seen_langs = set()

        manual_subs = info.get("subtitles", {}) or {}
        auto_subs = info.get("automatic_captions", {}) or {}

        all_subs = {}
        for lang, tracks in manual_subs.items():
            all_subs[lang] = (tracks, False)
        for lang, tracks in auto_subs.items():
            if lang not in all_subs:
                all_subs[lang] = (tracks, True)

        priority_langs = ["ar", "en", "en-US", "en-GB"]
        sorted_langs = sorted(
            all_subs.keys(),
            key=lambda x: (priority_langs.index(x) if x in priority_langs else 999, x)
        )

        for lang in sorted_langs:
            if lang in seen_langs:
                continue
            seen_langs.add(lang)

            tracks, is_auto = all_subs[lang]
            if not tracks:
                continue

            best_track = None
            for track in tracks:
                if track.get("ext") == "srt":
                    best_track = track
                    break
            if not best_track:
                for track in tracks:
                    if track.get("ext") == "vtt":
                        best_track = track
                        break
            if not best_track:
                best_track = tracks[0]

            if best_track and best_track.get("url"):
                result.append({
                    "language": lang,
                    "language_name": self._get_language_name(lang),
                    "url": best_track["url"],
                    "format": best_track.get("ext", "srt"),
                    "is_auto": is_auto,
                })

        return result[:10]

    def _get_language_name(self, code: str) -> str:
        names = {
            "ar": "🇸🇦 العربية",
            "en": "🇬🇧 English",
            "en-US": "🇺🇸 English (US)",
            "en-GB": "🇬🇧 English (UK)",
            "es": "🇪🇸 Español",
            "fr": "🇫🇷 Français",
            "de": "🇩🇪 Deutsch",
            "it": "🇮🇹 Italiano",
            "pt": "🇵🇹 Português",
            "ru": "🇷🇺 Русский",
            "ja": "🇯🇵 日本語",
            "ko": "🇰🇷 한국어",
            "zh": "🇨🇳 中文",
            "tr": "🇹🇷 Türkçe",
            "hi": "🇮🇳 हिन्दी",
            "ur": "🇵🇰 اردو",
        }
        return names.get(code, f"🌐 {code.upper()}")