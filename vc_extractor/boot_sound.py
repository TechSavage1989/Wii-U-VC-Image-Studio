from __future__ import annotations

from pathlib import Path
import os
import struct
import threading
import wave


class BootSoundConversionError(ValueError):
    """Raised when a selected boot sound cannot be converted to PCM WAV."""


def _temporary_wav_path(destination: Path) -> Path:
    return destination.with_name(
        f".{destination.stem}.{os.getpid()}.{threading.get_ident()}.tmp.wav"
    )


def _write_pcm_wav(
    destination: Path,
    pcm_data: bytes,
    *,
    channels: int,
    sample_width: int,
    sample_rate: int,
) -> None:
    with wave.open(str(destination), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sample_width)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm_data)


def _convert_btsnd(source: Path, destination: Path) -> None:
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise BootSoundConversionError(f"The BTSND file could not be read: {exc}") from exc

    if len(data) < 12:
        raise BootSoundConversionError("The BTSND file is too small to contain audio.")

    sound_type, loop_start = struct.unpack_from(">Ii", data, 0)
    if sound_type not in {0, 2}:
        raise BootSoundConversionError("The BTSND header is not recognized.")

    pcm_big_endian = data[8:]
    if not pcm_big_endian or len(pcm_big_endian) % 4:
        raise BootSoundConversionError(
            "The BTSND audio payload is not valid stereo 16-bit PCM."
        )

    frame_count = len(pcm_big_endian) // 4
    if loop_start >= frame_count:
        raise BootSoundConversionError("The BTSND loop position is outside the audio data.")
    if frame_count > 960_000:
        raise BootSoundConversionError("The BTSND file exceeds the Wii U format limit.")

    pcm_little_endian = bytearray(len(pcm_big_endian))
    pcm_little_endian[0::2] = pcm_big_endian[1::2]
    pcm_little_endian[1::2] = pcm_big_endian[0::2]
    _write_pcm_wav(
        destination,
        pcm_little_endian,
        channels=2,
        sample_width=2,
        sample_rate=48_000,
    )


def _copy_pcm_wav(source: Path, destination: Path) -> bool:
    """Copy an uncompressed PCM WAV through the standard WAV reader.

    Returns False when the WAV needs miniaudio decoding instead.
    """

    try:
        with wave.open(str(source), "rb") as handle:
            if handle.getcomptype() != "NONE":
                return False
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            frame_count = handle.getnframes()
            if channels not in {1, 2} or sample_width != 2:
                return False
            if sample_rate <= 0 or frame_count <= 0:
                raise BootSoundConversionError("The WAV file does not contain playable audio.")
            pcm_data = handle.readframes(frame_count)
    except (OSError, EOFError, wave.Error):
        return False

    _write_pcm_wav(
        destination,
        pcm_data,
        channels=channels,
        sample_width=sample_width,
        sample_rate=sample_rate,
    )
    return True


def _convert_with_miniaudio(source: Path, destination: Path) -> None:
    try:
        import miniaudio
    except ImportError as exc:
        raise BootSoundConversionError(
            "MP3 or encoded WAV conversion is unavailable because the bundled audio decoder could not be loaded."
        ) from exc

    try:
        info = miniaudio.get_file_info(str(source))
        channels = max(1, min(2, int(info.nchannels or 2)))
        sample_rate = max(8_000, min(192_000, int(info.sample_rate or 44_100)))
        decoded = miniaudio.decode_file(
            str(source),
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=channels,
            sample_rate=sample_rate,
        )
        if decoded.num_frames <= 0:
            raise BootSoundConversionError("The selected audio file contains no playable frames.")
        miniaudio.wav_write_file(str(destination), decoded)
    except BootSoundConversionError:
        raise
    except Exception as exc:
        raise BootSoundConversionError(f"The audio decoder could not convert this file: {exc}") from exc


def convert_boot_sound_to_wav(source: Path | str, destination: Path | str) -> Path:
    """Convert WAV, MP3, or Wii U BTSND audio to an atomic WAV output."""

    source_path = Path(source)
    destination_path = Path(destination)
    suffix = source_path.suffix.casefold()
    if suffix not in {".wav", ".mp3", ".btsnd"}:
        raise BootSoundConversionError("Choose a WAV, MP3, or BTSND boot-sound file.")
    if not source_path.is_file():
        raise BootSoundConversionError("The selected boot-sound file is missing.")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_wav_path(destination_path)
    try:
        if suffix == ".btsnd":
            _convert_btsnd(source_path, temporary)
        elif suffix == ".wav" and _copy_pcm_wav(source_path, temporary):
            pass
        else:
            _convert_with_miniaudio(source_path, temporary)
        if not temporary.is_file() or temporary.stat().st_size <= 44:
            raise BootSoundConversionError("The converted WAV file contains no playable audio.")
        temporary.replace(destination_path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return destination_path
