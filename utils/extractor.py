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
        cookies_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "cookies.txt"
        )

        self.base_opts = {
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "ignoreerrors": False,
            "socket_timeout": 30,
            "retries": 3,
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

        if os.path.exists(cookies_path):
            self.base_opts["cookiefile"] = cookies_path

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

    def extract(self, url: str, preferred_quality: str = "best") -> Dict:
        url = self._clean_url(url)
        platform = self._detect_platform(url)
        opts = self._get_platform_opts(platform)

        try:
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

        except yt_dlp.utils.DownloadError as e:
            raise Exception(self._translate_error(str(e)))

    def _clean_url(self, url: str) -> str:
        url = url.strip()
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

    def _get_platform_opts(self, platform: str) -> Dict:
        opts = {**self.base_opts}
        if platform == "youtube":
            opts["extractor_args"] = {
                "youtube": {
                    "player_client": ["web", "android"],
                    "player_skip": ["configs"]
                }
            }
        elif platform == "instagram":
            opts["http_headers"] = {
                **self.base_opts["http_headers"],
                "X-IG-App-ID": "936619743392459",
            }
        return opts

    def _build_result(self, info: Dict, platform: str) -> Dict:
        title = info.get("title", "Unknown")
        thumbnail = info.get("thumbnail", "")
        duration = info.get("duration", 0)
        uploader = info.get("uploader", "")
        description = info.get("description", "")

        qualities = self._extract_qualities(info)
        audio_only = self._extract_audio(info)

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
            height = f.get("height")
            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")
            url = f.get("url")

            if not url or not height or vcodec == "none":
                continue

            quality_label = f"{height}p"
            has_audio = acodec != "none"
            ext = f.get("ext", "mp4")
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
                "height": height,
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

            if vcodec != "none" or acodec == "none" or not url:
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
            "unsupported url": "هذا الرابط غير مدعوم",
        }
        for key, translation in translations.items():
            if key in error_lower:
                return translation
        return f"خطأ: {error[:200]}"