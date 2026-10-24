# Voice input: records the microphone in the plugin itself and turns speech
# into text through the gateway's /audio/transcriptions. Recording in Python
# (Windows MCI via ctypes, no extra packages) works the same for the browser
# chat and for a chat window inside the slicer, with no browser permissions.
#
# Thread rules for callers: start(), stop() and cancel() are quick and safe from
# the Qt main thread (MCI runs on its own thread); transcribe() blocks on the
# network, so call it from a worker thread. Failures raise VoiceError, an
# llm.LLMError with a message that can be shown to the user as is.
import json
import os
import queue
import re
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import OrderedDict

from .agent import config, llm

# Stored in steslicer.cfg next to the agent's settings (config.PREFIX).
DEFAULTS = OrderedDict([
    ("voice_engine", "auto"),     # auto | plugin | server | browser, see web/app.js
    ("voice_model", ""),          # empty = pick a *transcribe* / whisper model from /models
    ("voice_autosend", False),    # send the recognized text right away
])
ENGINES = ("auto", "plugin", "server", "browser")
MAX_AUDIO = 20 * 1024 * 1024
MAX_RECORD_SEC = 300              # a forgotten recording is dropped after this
# Vocabulary hint for the speech model, so print terms come out spelled right.
# Kept to one short sentence: on short recordings the model sometimes echoes
# the prompt into the text (see _strip_prompt_echo).
TRANSCRIBE_PROMPT = ("Стерео, STE Slicer, 3D-печать: Proto PLA, Enduse PETG, Fiberpart PA12, Sealant TPU, режимы 5D и "
                     "5D Spiral, высота слоя, поддержки, кайма, ретракт.")


class VoiceError(llm.LLMError):
    pass


# ---------- settings

def _prefs():
    from UM.Application import Application
    return Application.getInstance().getPreferences()


def init():
    prefs = _prefs()
    for key, default in DEFAULTS.items():
        prefs.addPreference(config.PREFIX + key, default)


def get(key):
    value = _prefs().getValue(config.PREFIX + key)
    default = DEFAULTS[key]
    if value is None:
        return default
    if isinstance(default, bool):
        return value.strip().lower() in ("1", "true", "yes", "on") if isinstance(value, str) else bool(value)
    return str(value)


def set_many(values):
    if not values:
        return
    prefs = _prefs()
    for key, value in values.items():
        if key not in DEFAULTS or (key == "voice_engine" and value not in ENGINES):
            continue
        if key == "voice_model":
            value = " ".join(str(value or "").split())  # it goes into a multipart field
            forget_model()
        prefs.setValue(config.PREFIX + key, value)
    try:
        from UM.Application import Application
        Application.getInstance().savePreferences()
    except Exception:
        pass


def public():
    data = {key: get(key) for key in DEFAULTS}
    data["voice_native"] = is_available()
    return data


# ---------- recording (Windows MCI)

def is_available():
    """True when the plugin can record by itself (Windows with winmm)."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.winmm.mciSendStringW)
    except Exception:
        return False


class _Recorder(object):
    ALIAS = "steai_voice"

    def __init__(self):
        self._jobs = queue.Queue()
        self._thread = None
        self._started = None
        self._timer = None
        self._bits = 16

    # MCI calls all go through one thread, whoever asks for them.
    def _run(self, fn):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="ste-ai-voice", daemon=True)
            self._thread.start()
        done = threading.Event()
        box = {}
        self._jobs.put((fn, box, done))
        if not done.wait(15):
            raise VoiceError("Звуковая система не отвечает, попробуйте ещё раз")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _loop(self):
        while True:
            fn, box, done = self._jobs.get()
            try:
                box["result"] = fn()
            except Exception as e:
                box["error"] = e if isinstance(e, VoiceError) else VoiceError("Ошибка записи звука: %s" % e)
            done.set()

    # (samples per second, channels); (0, 0) = the device's own default format
    FORMATS = ((16000, 1), (44100, 1), (48000, 1), (44100, 2), (48000, 2), (0, 0))

    def _mci(self, command):
        import ctypes
        out = ctypes.create_unicode_buffer(256)
        err = ctypes.windll.winmm.mciSendStringW(command, out, 256, None)
        if err:
            msg = ctypes.create_unicode_buffer(256)
            ctypes.windll.winmm.mciGetErrorStringW(err, msg, 256)
            raise VoiceError("Не удалось записать звук: %s (код MCI %d). Проверьте, что микрофон подключён "
                             "и включён в параметрах звука Windows." % (msg.value or "ошибка", err))
        return out.value

    def _close(self):
        try:
            self._mci("close " + self.ALIAS)
        except VoiceError:
            pass

    def recording(self):
        return self._started is not None

    def start(self):
        if not is_available():
            raise VoiceError("Запись звука в плагине работает только на Windows")

        def job():
            if self._started is not None:
                return
            # Some drivers refuse 16 kHz mono with MCI error 328 ("no installed
            # wave device can record files in the current format"), so try the
            # formats a microphone is likely to support, smallest first.
            error = None
            for rate, channels in self.FORMATS:
                self._close()
                self._mci("open new type waveaudio alias " + self.ALIAS)
                try:
                    self._bits = 16 if rate else 8
                    if rate:
                        align = 2 * channels
                        self._mci("set %s time format ms bitspersample 16 channels %d samplespersec %d "
                                  "bytespersec %d alignment %d" % (self.ALIAS, channels, rate, rate * align, align))
                    self._mci("record " + self.ALIAS)
                except VoiceError as e:
                    error = e
                    continue
                self._started = time.time()
                return
            self._close()
            raise error

        self._run(job)
        self._arm_timer()

    def stop(self):
        """Ends the recording and returns it as WAV bytes."""
        def job():
            if self._started is None:
                raise VoiceError("Запись не идёт: нажмите микрофон, чтобы начать")
            self._started = None
            path = os.path.join(_short_temp_dir(), "steai_voice_%d.wav" % os.getpid())
            try:
                self._mci("stop " + self.ALIAS)
                self._mci('save %s "%s"' % (self.ALIAS, path))
            finally:
                self._close()
            try:
                with open(path, "rb") as f:
                    return f.read()
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass

        self._disarm_timer()
        return self._run(job)

    def level(self):
        """The current input peak while recording (MCI "status level"), as a
        fraction of full scale, or None when the device does not report it."""
        def job():
            if self._started is None:
                return None
            try:
                value = int(self._mci("status %s level" % self.ALIAS) or 0)
            except (VoiceError, ValueError):
                return None
            return value / (32767.0 if self._bits == 16 else 127.0)

        if not is_available():
            return None
        return self._run(job)

    def cancel(self):
        def job():
            self._started = None
            self._close()

        self._disarm_timer()
        if is_available():
            self._run(job)

    def _arm_timer(self):
        self._disarm_timer()
        self._timer = threading.Timer(MAX_RECORD_SEC, self.cancel)
        self._timer.daemon = True
        self._timer.start()

    def _disarm_timer(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


def _short_temp_dir():
    # MCI may fail on paths with spaces or Cyrillic (C:\Users\Ник\...); the 8.3
    # short form of the temp folder is plain ASCII.
    path = tempfile.gettempdir()
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(512)
        if ctypes.windll.kernel32.GetShortPathNameW(path, buf, 512):
            return buf.value
    except Exception:
        pass
    return path


class _WaveInRecorder(object):
    """Records with the waveIn API (winmm via ctypes): 16 kHz mono 16-bit through
    the wave mapper, which converts from whatever the microphone delivers. Unlike
    MCI it hands over the samples as they come, so the input level is known for
    the pause detection and the waves around the microphone button."""
    RATE = 16000
    CHUNK = 1600 * 2          # 100 ms of 16-bit mono
    BUFFERS = 8

    def __init__(self, winmm=None):
        self._winmm = winmm
        self._handle = None
        self._headers = []
        self._chunks = []
        self._peak = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._started = None

    def _api(self):
        if self._winmm is None:
            import ctypes
            self._winmm = ctypes.windll.winmm
        return self._winmm

    def _check(self, err, what):
        if err:
            raise VoiceError("Не удалось записать звук: %s вернул код %d. Проверьте, что микрофон подключён "
                             "и включён в параметрах звука Windows." % (what, err))

    def recording(self):
        return self._started is not None

    def start(self):
        import ctypes
        if self._started is not None:
            return
        api = self._api()
        fmt = _WAVEFORMATEX(1, 1, self.RATE, self.RATE * 2, 2, 16, 0)
        handle = ctypes.c_void_p()
        self._check(api.waveInOpen(ctypes.byref(handle), ctypes.c_uint(0xFFFFFFFF), ctypes.byref(fmt), 0, 0, 0),
                    "waveInOpen")
        self._handle = handle
        self._chunks = []
        self._peak = 0.0
        self._headers = []
        try:
            for _ in range(self.BUFFERS):
                buf = ctypes.create_string_buffer(self.CHUNK)
                hdr = _WAVEHDR()
                hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
                hdr.dwBufferLength = self.CHUNK
                self._headers.append((hdr, buf))
                self._check(api.waveInPrepareHeader(handle, ctypes.byref(hdr), ctypes.sizeof(hdr)), "waveInPrepareHeader")
                self._check(api.waveInAddBuffer(handle, ctypes.byref(hdr), ctypes.sizeof(hdr)), "waveInAddBuffer")
            self._check(api.waveInStart(handle), "waveInStart")
        except VoiceError:
            self._close()
            raise
        self._stop.clear()
        self._started = time.time()
        self._thread = threading.Thread(target=self._pump, name="ste-ai-wavein", daemon=True)
        self._thread.start()

    def _take(self, hdr, buf):
        """A filled buffer: keep its samples, note the peak, give it back to the driver."""
        import ctypes
        data = buf.raw[:hdr.dwBytesRecorded]
        if data:
            self._chunks.append(data)
            self._peak = _peak16(data)
        hdr.dwBytesRecorded = 0
        hdr.dwFlags &= ~WHDR_DONE
        return data

    def _pump(self):
        import ctypes
        api = self._api()
        while not self._stop.is_set():
            with self._lock:
                for hdr, buf in self._headers:
                    if hdr.dwFlags & WHDR_DONE:
                        self._take(hdr, buf)
                        api.waveInAddBuffer(self._handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
            if time.time() - (self._started or time.time()) > MAX_RECORD_SEC:
                break
            time.sleep(0.02)

    def _close(self):
        import ctypes
        api = self._api()
        if self._handle is None:
            return
        try:
            api.waveInStop(self._handle)
            api.waveInReset(self._handle)   # marks the buffers still queued as done
            for hdr, buf in self._headers:
                if hdr.dwFlags & WHDR_DONE and hdr.dwBytesRecorded:
                    self._take(hdr, buf)
                api.waveInUnprepareHeader(self._handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
        finally:
            api.waveInClose(self._handle)
            self._handle = None
            self._headers = []

    def _finish(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)
            self._thread = None
        with self._lock:
            self._close()
        self._started = None

    def stop(self):
        """Ends the recording and returns it as WAV bytes."""
        if self._started is None:
            raise VoiceError("Запись не идёт: нажмите микрофон, чтобы начать")
        self._finish()
        pcm = b"".join(self._chunks)
        self._chunks = []
        return _wav16(pcm, self.RATE)

    def cancel(self):
        if self._started is not None:
            self._finish()
        self._chunks = []

    def level(self):
        return self._peak if self._started is not None else None


WHDR_DONE = 0x1


def _peak16(data):
    """Peak of 16-bit little-endian samples, 0..1. numpy, not the array module:
    the slicer's bundled Python has only the stdlib modules the slicer itself uses."""
    import numpy
    samples = numpy.frombuffer(data[:len(data) - len(data) % 2], dtype="<i2")
    if not samples.size:
        return 0.0
    return min(1.0, float(numpy.abs(samples.astype(numpy.int32)).max()) / 32768.0)


def _wav16(pcm, rate):
    """A mono 16-bit WAV file around raw samples (no `wave` module in the slicer's Python)."""
    import struct
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def _structs():
    import ctypes

    class WAVEFORMATEX(ctypes.Structure):
        _fields_ = [("wFormatTag", ctypes.c_ushort), ("nChannels", ctypes.c_ushort),
                    ("nSamplesPerSec", ctypes.c_uint32), ("nAvgBytesPerSec", ctypes.c_uint32),
                    ("nBlockAlign", ctypes.c_ushort), ("wBitsPerSample", ctypes.c_ushort),
                    ("cbSize", ctypes.c_ushort)]

    class WAVEHDR(ctypes.Structure):
        pass
    WAVEHDR._fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", ctypes.c_uint32),
                        ("dwBytesRecorded", ctypes.c_uint32), ("dwUser", ctypes.c_size_t),
                        ("dwFlags", ctypes.c_uint32), ("dwLoops", ctypes.c_uint32),
                        ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]
    return WAVEFORMATEX, WAVEHDR


_WAVEFORMATEX, _WAVEHDR = _structs()


class _Voice(object):
    """waveIn first (levels for the pause detection and the waves); MCI when
    waveIn cannot open the microphone."""

    def __init__(self):
        self.wavein = _WaveInRecorder()
        self.mci = _Recorder()
        self.active = None

    def start(self):
        if not is_available():
            raise VoiceError("Запись звука в плагине работает только на Windows")
        if self.active is not None and self.active.recording():
            return
        try:
            self.wavein.start()
            self.active = self.wavein
        except Exception:
            self.mci.start()
            self.active = self.mci

    def stop(self):
        if self.active is None:
            raise VoiceError("Запись не идёт: нажмите микрофон, чтобы начать")
        active, self.active = self.active, None
        return active.stop()

    def cancel(self):
        active, self.active = self.active, None
        if active is not None:
            active.cancel()

    def level(self):
        """The input peak, 0..1, or None (not recording, or MCI that does not report it)."""
        return self.active.level() if self.active is not None else None


recorder = _Voice()
start = recorder.start
stop = recorder.stop
cancel = recorder.cancel
level = recorder.level


class SilenceDetector(object):
    """Ends dictation after a pause: fed the input level every ~0.25 s, says
    "stop" once speech was heard and then `pause` seconds passed below the
    speech threshold. The threshold follows the room's noise floor. If the
    device reports no level, or the level never moves, it never stops on its
    own and the user stops with the button."""

    def __init__(self, pause=4.0):
        self.pause = pause
        self.floor = None
        self.heard = False
        self.quiet_since = None

    def feed(self, value, now):
        if value is None:
            return False
        # noise floor: follows quiet levels at once, louder ones only slowly
        if self.floor is None or value < self.floor:
            self.floor = value
        else:
            self.floor += (value - self.floor) * 0.01
        speaking = value > max(self.floor * 3, self.floor + 0.04)
        if speaking:
            self.heard = True
            self.quiet_since = None
            return False
        if not self.heard:
            return False
        if self.quiet_since is None:
            self.quiet_since = now
        return now - self.quiet_since >= self.pause


# ---------- speech to text

_model_cache = {}


def forget_model():
    """Call when the gateway (URL or key) changes: it may name the model differently."""
    _model_cache.clear()


def _pick_model():
    """The gateway's speech-to-text model: the one set in the settings, else the
    best *transcribe* / whisper model it lists, else the OpenAI default name."""
    chosen = get("voice_model").strip()
    if chosen:
        return chosen
    if "model" in _model_cache:
        return _model_cache["model"]
    try:
        models = llm.list_models()
    except llm.LLMError:
        models = []

    def rank(name):
        low = name.lower()
        if "transcribe" in low:
            return 0 if "mini" in low else 1
        return 2 if "whisper" in low else 3

    found = sorted((m for m in models if rank(m) < 3), key=rank)
    _model_cache["model"] = found[0] if found else "gpt-4o-mini-transcribe"
    return _model_cache["model"]


def transcribe(audio, mime="audio/wav"):
    """POST <llm_url>/audio/transcriptions (OpenAI-compatible); the key stays in
    the plugin. Returns {"text", "model"}."""
    if not audio:
        raise VoiceError("Пустая запись: микрофон ничего не передал")
    if len(audio) > MAX_AUDIO:
        raise VoiceError("Слишком длинная запись, разбейте на части")
    mime = (mime or "audio/wav").split(";")[0].strip()
    ext = {"audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav",
           "audio/webm": "webm"}.get(mime, "wav")
    model = _pick_model()
    boundary = "steai" + uuid.uuid4().hex
    parts = []
    for name, value in (("model", model), ("language", "ru"), ("prompt", TRANSCRIBE_PROMPT),
                        ("response_format", "json")):
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                      % (boundary, name, value)).encode("utf-8"))
    parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="speech.%s"\r\n'
                  'Content-Type: %s\r\n\r\n' % (boundary, ext, mime)).encode("utf-8"))
    parts.append(audio)
    parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    headers = {"Content-Type": "multipart/form-data; boundary=" + boundary, "Accept": "application/json"}
    key = config.get("llm_key") or ""
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(llm._endpoint("/audio/transcriptions"), data=b"".join(parts),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60, context=llm._ssl_context()) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            detail = ""
        forget_model()  # re-pick next time
        if e.code in (401, 403):
            raise VoiceError("Шлюз отклонил ключ при распознавании речи (HTTP %s). %s" % (e.code, detail))
        raise VoiceError("Шлюз не распознал речь моделью %s (HTTP %s): %s" % (model, e.code, detail))
    except ssl.SSLError as e:
        raise VoiceError("Ошибка SSL при распознавании речи: %s" % e)
    except urllib.error.URLError as e:
        raise VoiceError("Не удалось подключиться к шлюзу для распознавания речи: %s" % e.reason)
    try:
        text = json.loads(raw).get("text", "")
    except (ValueError, AttributeError):
        text = raw  # response_format=text on some gateways
    return {"text": strip_address(_strip_prompt_echo(text or "")), "model": model}


_ADDRESS = re.compile(r"^\s*(?:(?:эй|слушай|ок|окей|привет)[\s,]+)?стерео\b[\s,.!:;—–-]*", re.IGNORECASE)


def strip_address(text):
    """"Стерео, сделай …" → "Сделай …": the assistant's name at the start of a
    phrase is an address, not part of the request."""
    rest = _ADDRESS.sub("", text, count=1)
    if rest == text or not rest:
        return text
    return rest[:1].upper() + rest[1:]


def _words(text):
    return re.findall(r"[\w°]+", text.lower())


def _strip_prompt_echo(text):
    """Drops sentences that are the prompt repeated back rather than speech:
    contained in it, or made almost only of its words."""
    prompt_flat = " ".join(_words(TRANSCRIBE_PROMPT))
    prompt_words = set(_words(TRANSCRIBE_PROMPT))
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        words = _words(sentence)
        if words and len(" ".join(words)) >= 12 and " ".join(words) in prompt_flat:
            continue
        if len(words) >= 5 and sum(w in prompt_words for w in words) >= 0.8 * len(words):
            continue
        kept.append(sentence)
    return " ".join(kept).strip()
