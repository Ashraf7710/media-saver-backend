import yt_dlp
import re
import os
import tempfile
from typing import Dict, List, Optional
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)

IMAGE_EXTS = ("jpg", "jpeg", "png", "gif", "webp", "bmp", "avif", "svg", "heic", "jfif")
AUDIO_EXTS = ("mp3", "m4a", "aac", "ogg", "opus", "wav", "flac", "wma", "oga", "mid", "midi")
VIDEO_EXTS = ("mp4", "webm", "mkv", "mov", "avi", "flv", "3gp", "m4v", "ogv", "ts", "mpeg", "mpg")

class ExtractionFailed(Exception):
    """Exception carrying a machine-readable error type alongside the Arabic message."""
    def __init__(self, message: str, error_type: str = "extraction_failed"):
        super().__init__(message)
        self.error_type = error_type

class MediaExtractor:
    QUALITY_ORDER = {
        "4320p": 1, "2160p": 2, "1440p": 3, "1080p": 4,
        "720p": 5, "480p": 6, "360p": 7, "240p": 8, "144p": 9
    }

    def __init__(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.cookies_path = os.path.join(base_dir, "cookies.txt")
        self.instagram_cookies_path = os.path.join(base_dir, "instagram_cookies.txt")

        # 🛡️ yt-dlp يعيد كتابة ملف الكوكيز (save_cookies) عند كل استخدام فيقتصّه.
        # لذا نخزّن المحتوى في الذاكرة ونمرّر نسخة مؤقتة لكل طلب لحماية الملف الأصلي
        # من التلف/التعارض بين عمال gunicorn.
        self._cookies_cache = {}
        for path in (self.cookies_path, self.instagram_cookies_path):
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        self._cookies_cache[os.path.abspath(path)] = f.read()
                except Exception:
                    self._cookies_cache[os.path.abspath(path)] = ""
        self._temp_cookie_files: List[str] = []

        self.base_opts = {
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "ignoreerrors": False,
            "socket_timeout": 15,
            "retries": 2,
            "skip_download": True,
            # ✅ تجاوز قيود العمر - كل المواقع
            "age_limit": 99,
            "geo_bypass": True,
            "geo_bypass_country": "US",
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

        direct = self._direct_media_info(url)
        if direct:
            return direct

        platform = self._detect_platform(url)

        attempts = self._build_attempts(platform)
        last_error = None

        try:
            for name, use_cookies, client in attempts:
                try:
                    logger.info(f"Attempt [{name}] {url[:80]}")
                    opts = self._get_platform_opts(platform, use_cookies=use_cookies, client=client)

                    if start_time or end_time:
                        opts["download_ranges"] = self._build_ranges(start_time, end_time)
                        opts["force_keyframes_at_cuts"] = True

                    return self._do_extract(url, opts, platform)
                except Exception as e:
                    last_error = e
                    logger.warning(f"Attempt [{name}] failed: {str(e)[:200]}")
        finally:
            self._cleanup_temp_cookies()

        raw_error = str(last_error)
        raise ExtractionFailed(self._translate_error(raw_error), self._classify_error(raw_error))

    def _classify_error(self, error: str) -> str:
        e = error.lower()
        if any(k in e for k in ("confirm you're not a bot", "not a bot", "bot check", "too many requests")):
            return "bot_check"
        if any(k in e for k in ("private", "login required", "sign in", "logged out")):
            return "private_content"
        if any(k in e for k in ("not found", "404", "could not find", "empty media response")):
            return "not_found"
        if any(k in e for k in ("geo restricted", "country")):
            return "geo_restricted"
        if any(k in e for k in ("copyright", "removed")):
            return "copyright"
        if any(k in e for k in ("age-restricted", "age restricted", "age gated")):
            return "age_restricted"
        if any(k in e for k in ("live stream", "is live")):
            return "live_stream"
        if any(k in e for k in ("unsupported url", "not a valid url", "unsupported")):
            return "unsupported_platform"
        if "http error 403" in e:
            return "rate_limited"
        if "http error 429" in e or "too many" in e:
            return "rate_limited"
        return "extraction_failed"

    def _build_attempts(self, platform: str) -> List[tuple]:
        if platform == "youtube":
            return [
                ("youtube-default-client+cookies", True, "default"),
                ("youtube-android-vr-no-cookies", False, "android_vr"),
                ("youtube-android-client-no-cookies", False, "android"),
                ("youtube-tv-client-no-cookies", False, "tv"),
                ("youtube-web_embedded-no-cookies", False, "web_embedded"),
                ("youtube-default-client-no-cookies", False, "default"),
            ]
        if platform == "instagram":
            return [
                ("instagram-web+cookies", True, "default"),
                ("instagram-web_embedded", False, "web_embedded"),
                ("instagram-mobile", False, "mobile"),
                ("instagram-app", False, "app"),
            ]
        return [
            (f"{platform}-default+cookies", True, "default"),
            (f"{platform}-no-cookies", False, "default"),
        ]

    def _do_extract(self, url: str, opts: Dict, platform: str) -> Dict:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise Exception("لم يتم العثور على محتوى")

        if info.get("_type") == "playlist" and platform != "instagram":
            # Instagram carousels جاية كـ playlist؛ نُبقيها لاستخراج كل العناصر
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
        # ✅ إزالة معاملات التتبع من Instagram
        url = re.sub(r'[?&](igsh=[^&]*|utm_[^&]*|si=[^&]*|feature=[^&]*)', '', url)
        url = re.sub(r'\?$', '', url)

        # ✅ تطبيع روابط Instagram
        if "instagram.com" in url or "instagr.am" in url:
            url = url.replace("m.instagram.com", "www.instagram.com")
            # instagram.com/USER/p/CODE/ → instagram.com/p/CODE/
            url = re.sub(
                r'(?:https?://(?:www\.)?instagram\.com)/[A-Za-z0-9._]+/p/([A-Za-z0-9_-]+)',
                r'https://www.instagram.com/p/\1', url
            )
            # /reels/CODE/ (جمع) → /reel/CODE/
            url = re.sub(
                r'instagram\.com/reels/([A-Za-z0-9_-]+)',
                r'instagram.com/reel/\1', url
            )
            # روابط /share/ → لا يمكن استخراجها مباشرة
            if "instagram.com/share/" in url:
                raise Exception("افتح الرابط في المتصفح ثم انسخ رابط الصفحة النهائي")

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

    @staticmethod
    def _url_extension(url: str) -> Optional[str]:
        try:
            path = urlparse(url).path
        except Exception:
            return None
        if not path:
            return None
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        return ext or None

    @staticmethod
    def _classify_media(ext: Optional[str], vcodec: str = "none",
                        acodec: str = "none", height: int = 0,
                        url: str = "") -> str:
        if ext in IMAGE_EXTS:
            return "image"
        if ext in AUDIO_EXTS:
            return "audio"
        if vcodec != "none" or height:
            return "video"
        if acodec != "none":
            return "audio"
        if ext in VIDEO_EXTS:
            return "video"
        if url and any(u in url.lower() for u in (".cdninstagram.", ".fbcdn.", "scontent")):
            return "image"
        return "video"

    def _direct_media_info(self, url: str) -> Optional[Dict]:
        ext = self._url_extension(url)
        if not ext or ext not in IMAGE_EXTS + AUDIO_EXTS + VIDEO_EXTS:
            return None

        media_type = self._classify_media(ext)
        basename = os.path.basename(urlparse(url).path)
        title = self._clean_title(os.path.splitext(basename)[0] if basename else "media")

        if media_type == "image":
            label, has_audio = "صورة", False
        elif media_type == "audio":
            label, has_audio = "Audio", True
        else:
            label, has_audio = "HD", True

        return {
            "title": title or "media",
            "thumbnail": "",
            "duration": 0,
            "platform": "Other",
            "uploader": "",
            "description": "",
            "qualities": [{
                "quality": label,
                "url": url,
                "format": ext,
                "filesize": 0,
                "has_audio": has_audio,
                "vcodec": "none" if media_type != "video" else "h264",
                "acodec": "none" if media_type == "image" else "aac",
                "fps": None,
                "height": 0,
                "width": 0,
                "media_type": media_type,
            }],
            "audio_only": [{
                "quality": "Audio",
                "url": url,
                "format": ext,
                "filesize": 0,
                "abr": 0,
                "acodec": "aac",
            }] if media_type == "audio" else [],
            "subtitles": [],
        }

    def _cookie_file(self, path: str) -> Optional[str]:
        raw = self._cookies_cache.get(os.path.abspath(path))
        if not raw:
            return None
        fd, tmp = tempfile.mkstemp(prefix="ms_cookies_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
        self._temp_cookie_files.append(tmp)
        return tmp

    def _cleanup_temp_cookies(self):
        for tmp in self._temp_cookie_files:
            try:
                os.remove(tmp)
            except OSError:
                pass
        self._temp_cookie_files.clear()

    def _get_platform_opts(self, platform: str, use_cookies: bool = True, client: str = "default") -> Dict:
        opts = {**self.base_opts}

        if use_cookies:
            cf = self._cookie_file(self.cookies_path)
            if cf:
                opts["cookiefile"] = cf

        opts["format"] = "best"

        if platform == "youtube":
            # ✅ إعدادات YouTube
            po_token = os.environ.get("YOUTUBE_PO_TOKEN", "").strip()
            if client == "default" and not po_token:
                opts.pop("extractor_args", None)
            else:
                extractor_args: Dict[str, Dict] = {"youtube": {}}
                if client != "default":
                    extractor_args["youtube"]["player_client"] = [client]
                if po_token:
                    # مثال: "ios.gvs+xxx" أو اسم مزوّد PO Token
                    extractor_args["youtube"]["po_token"] = po_token
                opts["extractor_args"] = extractor_args
            opts["format"] = "bestvideo*+bestaudio/best"
            # الـ proxy فقط في المحاولة الأولى (عند استخدام الكوكيز)
            if use_cookies:
                proxy_url = os.environ.get("YOUTUBE_PROXY")
                if proxy_url:
                    opts["proxy"] = proxy_url

        elif platform == "instagram":
            opts["http_headers"] = {
                **self.base_opts["http_headers"],
                "X-IG-App-ID": "936619743392459",
            }
            # ✅ اختيار Web Client (يساعد في تجاوز الحجب)
            if client != "default":
                opts["extractor_args"] = {
                    "instagram": {
                        "web_client": [client],
                    }
                }
            # ✅ دعم الصور والفيديوهات المتعددة
            opts["format"] = "best"
            # ✅ استخراج كل عناصر الـ Carousel
            opts["extract_flat"] = False
            # ✅ منشورات الصور المفردة: السماح بالعودة بالصور (بدون رفع خطأ no-video)
            opts["ignore_no_formats_error"] = True
            # ✅ استخدام cookies Instagram للـ Stories
            if use_cookies:
                cf = self._cookie_file(self.instagram_cookies_path)
                if cf:
                    opts["cookiefile"] = cf
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
                if not thumbnail and entries[0].get("thumbnail"):
                    thumbnail = entries[0]["thumbnail"]
                qualities = []
                for idx, entry in enumerate(entries, 1):
                    entry_url = entry.get("url") or entry.get("webpage_url")
                    if not entry_url:
                        # جرب formats
                        entry_formats = entry.get("formats", [])
                        if entry_formats:
                            entry_url = entry_formats[-1].get("url")

                    entry_is_image = False
                    entry_thumb = None
                    if not entry_url:
                        # عنصر صورة: أعلى دقة من thumbnails
                        entry_thumbs = [t for t in entry.get("thumbnails", []) if t.get("url")]
                        if entry_thumbs:
                            entry_thumb = max(entry_thumbs, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
                            entry_url = entry_thumb.get("url")
                            entry_is_image = True

                    if entry_url:
                        is_video = entry.get("vcodec", "none") != "none" or entry.get("ext") in ("mp4", "webm")
                        if entry_is_image:
                            is_video = False
                        ext = "jpg" if (entry_is_image or not is_video) else "mp4"
                        h = entry.get("height", 0)
                        w = entry.get("width", 0)
                        if entry_thumb:
                            h = entry_thumb.get("height") or h
                            w = entry_thumb.get("width") or w

                        qualities.append({
                            "quality": f"عنصر {idx}",
                            "url": entry_url,
                            "format": ext,
                            "filesize": entry.get("filesize") or entry.get("filesize_approx") or 0,
                            "has_audio": is_video,
                            "vcodec": entry.get("vcodec", "none"),
                            "acodec": entry.get("acodec", "none"),
                            "fps": entry.get("fps"),
                            "height": h,
                            "width": w,
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

            elif direct_url or info.get("thumbnails"):
                # صورة أو فيديو مفرد
                is_video = info.get("vcodec", "none") != "none" or info.get("ext") in ("mp4", "webm")
                height = info.get("height", 0)
                width = info.get("width", 0)

                media_url = direct_url
                if not media_url:
                    # منشور صورة: أعلى دقة من الصور المتاحة
                    thumbs = [t for t in info.get("thumbnails", []) if t.get("url")]
                    if not thumbs:
                        raise Exception("لم يتم العثور على روابط تحميل لهذا المنشور")
                    best_thumb = max(thumbs, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
                    media_url = best_thumb.get("url")
                    height = best_thumb.get("height") or height
                    width = best_thumb.get("width") or width
                    is_video = False

                ext = info.get("ext", "jpg" if not is_video else "mp4")
                if not is_video:
                    ext = "jpg"

                quality_label = f"{height}p" if height and is_video else ("📷 صورة" if not is_video else "HD")

                filesize = info.get("filesize") or info.get("filesize_approx") or 0
                if not filesize and duration and is_video:
                    tbr = info.get("tbr") or 0
                    if tbr:
                        filesize = int((tbr * 1000 / 8) * duration)

                qualities = [{
                    "quality": quality_label,
                    "url": media_url,
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
                vcodec = info.get("vcodec") or "none"
                acodec = info.get("acodec") or "none"
                height = info.get("height") or 0
                url_ext = self._url_extension(direct_url) or info.get("ext") or ""
                media_type = self._classify_media(url_ext, vcodec, acodec, height, direct_url)

                if media_type == "image":
                    ext, has_audio, label = url_ext or "jpg", False, "صورة"
                    vcodec, acodec = "none", "none"
                elif media_type == "audio":
                    ext, has_audio, label = url_ext or "mp3", True, "Audio"
                    vcodec = "none"
                else:
                    ext = url_ext or info.get("ext") or "mp4"
                    has_audio = acodec != "none"
                    label = f"{height}p" if height else "HD"

                qualities = [{
                    "quality": label,
                    "url": direct_url,
                    "format": ext,
                    "filesize": info.get("filesize", 0),
                    "has_audio": has_audio,
                    "vcodec": vcodec,
                    "acodec": acodec,
                    "fps": info.get("fps"),
                    "height": height,
                    "width": info.get("width", 0),
                    "media_type": media_type,
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

                # 🚫 استبعاد AV1: التطبيق لا يستطيع دمج/تشغيله بعد
                if "av01" in vcodec.lower() or self._simplify_codec(vcodec) == "AV1":
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
                    "media_type": "video",
                }

                if not has_audio:
                    # صوت متوافق مع حاوية الفيديو (Opus لـ webm، AAC لـ mp4)
                    best_audio = self._find_best_audio(formats, container_hint=ext)
                    if best_audio:
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

    def _find_best_audio(self, formats: List[Dict], container_hint: str = "mp4") -> Optional[Dict]:
        audio = [
            f for f in formats
            if f.get("vcodec", "none") == "none"
            and f.get("acodec", "none") != "none"
            and f.get("url")
        ]
        if not audio:
            return None
        audio.sort(key=lambda x: x.get("abr") or x.get("tbr") or 0, reverse=True)
        # فيديو WebM (VP9) يحتاج صوت Opus/Vorbis في نفس الحاوية
        if container_hint == "webm":
            for f in audio:
                if f.get("ext") == "webm":
                    return f
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
            "confirm you're not a bot": "يوتيوب يطلب تأكيد أنك لست بوتاً - جارٍ تجربة عميل مختلف",
            "not a bot": "يوتيوب يطلب تأكيد أنك لست بوتاً - جارٍ تجربة عميل مختلف",
            "sign in": "يتطلب تسجيل الدخول",
            "age-restricted": "محتوى مقيد بالعمر",
            "copyright": "تم إزالته بسبب حقوق النشر",
            "not found": "لم يتم العثور على المحتوى",
            "geo restricted": "غير متاح في منطقتك",
            "live stream": "البث المباشر غير مدعوم",
            "unsupported url": "هذا رابط غير مدعوم",
            "empty media response": "المنشور غير متاح أو تم حذفه، أو يجب تسجيل الدخول لإنستغرام",
            "login required": "يتطلب تسجيل الدخول لإنستغرام",
            "could not find": "لم يتم العثور على المحتوى",
            "there is no video in this post": "هذا منشور صورة - جارٍ جلب الصورة",
            "no video formats": "هذا منشور صورة - جارٍ جلب الصورة",
            "requested format is not available": "جودة الفيديو غير متاحة حالياً، جرّب رابطاً آخر",
            "no formats": "لا توجد صيغ متاحة لهذا الفيديو",
            "is not a valid url": "الرابط غير صالح",
            "http error 403": "الموقع حظر الطلب، حاول لاحقاً",
            "http error 429": "الموقع حظر الطلب بسبب التكرار، حاول لاحقاً",
            "unauthorized": "غير مصرح - تأكد من تحديث الكوكيز",
            "story not found": "الاستوري غير متاح أو انتهى وقتها",
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