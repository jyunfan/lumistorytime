#!/usr/bin/env python3
"""Generate narration MP3 files from transcript text files with ElevenLabs.

Examples:
  ELEVENLABS_API_KEY=... python scripts/elevenlabs_tts.py --voice-name Yui --ids 003
  ELEVENLABS_API_KEY=... python scripts/elevenlabs_tts.py --voice-name Yui --from-id 004 --to-id 010
  ELEVENLABS_API_KEY=... python scripts/elevenlabs_tts.py --voice-id VOICE_ID transcripts/001-the-fox-and-the-grapes.txt

The script intentionally uses only Python's standard library so it can run in a
fresh checkout without installing dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_multilingual_v2"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"
TRANSCRIPT_ID_RE = re.compile(r"^(\d{3})-")
STAGE_DIRECTION_RE = re.compile(r"(?m)^\[[^\]\n]+\]\s*")
ENGLISH_HESITATION_RE = re.compile(r"(?im)^\s*hmm[.。…]*\s*$")
LATIN_LETTER_RE = re.compile(r"[A-Za-z]")
ROMANIZED_BRAND_RE = re.compile(r"\bLumi\b", re.IGNORECASE)
ALLOWED_LATIN_TERMS = ("Lumi",)


class ElevenLabsError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate ElevenLabs MP3 files from transcript text files."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        help="Transcript files. If omitted, files are selected from --input-dir.",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("transcripts"),
        help="Directory containing transcript .txt files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("audio/voice"),
        help="Directory for generated MP3 files.",
    )
    parser.add_argument(
        "--ids",
        help="Comma-separated transcript ids, for example: 001,003,010.",
    )
    parser.add_argument("--from-id", help="First transcript id to include, e.g. 004.")
    parser.add_argument("--to-id", help="Last transcript id to include, e.g. 010.")
    parser.add_argument(
        "--voice-id",
        help="ElevenLabs voice id. Preferred for repeatable production runs.",
    )
    parser.add_argument(
        "--voice-name",
        help="Voice name to search in your ElevenLabs account, e.g. Yui.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"ElevenLabs model id. Default: {DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "--output-format",
        default=DEFAULT_OUTPUT_FORMAT,
        help=f"ElevenLabs output format. Default: {DEFAULT_OUTPUT_FORMAT}.",
    )
    parser.add_argument(
        "--language-code",
        default="zh",
        help="Optional language code sent to ElevenLabs. Use empty string to omit.",
    )
    parser.add_argument(
        "--stability",
        type=float,
        default=0.70,
        help="Voice stability, 0.0 to 1.0.",
    )
    parser.add_argument(
        "--similarity-boost",
        type=float,
        default=0.85,
        help="Voice similarity boost, 0.0 to 1.0.",
    )
    parser.add_argument(
        "--style",
        type=float,
        default=0.0,
        help="Style exaggeration, 0.0 to 1.0.",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=0.90,
        help="Speaking speed. Values below 1.0 are slower.",
    )
    parser.add_argument(
        "--no-speaker-boost",
        action="store_true",
        help="Disable speaker boost in voice settings.",
    )
    parser.add_argument(
        "--strip-stage-directions",
        action="store_true",
        default=True,
        help="Remove leading bracket cues like [excited] before sending text. Enabled by default.",
    )
    parser.add_argument(
        "--keep-stage-directions",
        dest="strip_stage_directions",
        action="store_false",
        help="Keep bracket cues. Useful only for models that intentionally use them, such as eleven_v3.",
    )
    parser.add_argument(
        "--keep-english-hesitations",
        action="store_true",
        help="Keep English hesitation lines like 'Hmm...' instead of normalizing them to '嗯...'.",
    )
    parser.add_argument(
        "--keep-romanized-brand",
        action="store_true",
        default=True,
        help="Keep romanized brand words like 'Lumi' instead of normalizing them to Chinese pronunciation.",
    )
    parser.add_argument(
        "--normalize-romanized-brand",
        dest="keep_romanized_brand",
        action="store_false",
        help="Normalize romanized brand words like 'Lumi' to Chinese pronunciation.",
    )
    parser.add_argument(
        "--allow-latin",
        action="store_true",
        help="Allow Latin letters in the text sent to ElevenLabs. By default, Latin letters fail the run.",
    )
    parser.add_argument(
        "--preview-text",
        action="store_true",
        help="Print the exact text that would be sent to ElevenLabs.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate files even if the output MP3 already exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned work without calling ElevenLabs.",
    )
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="List available voices and exit.",
    )
    parser.add_argument(
        "--api-key-env",
        default="ELEVENLABS_API_KEY",
        help="Environment variable containing the ElevenLabs API key.",
    )
    return parser.parse_args()


def get_api_key(env_name: str) -> str:
    api_key = os.environ.get(env_name, "").strip()
    if not api_key:
        raise ElevenLabsError(f"Missing API key. Set {env_name}=...")
    return api_key


def request_json(api_key: str, path: str, params: dict[str, Any] | None = None) -> Any:
    query = f"?{urlencode(params)}" if params else ""
    request = Request(
        f"{API_BASE}{path}{query}",
        headers={"xi-api-key": api_key, "Accept": "application/json"},
        method="GET",
    )
    return json.loads(read_response(request).decode("utf-8"))


def post_audio(
    api_key: str,
    voice_id: str,
    payload: dict[str, Any],
    output_format: str,
) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{API_BASE}/v1/text-to-speech/{voice_id}?{urlencode({'output_format': output_format})}",
        data=body,
        headers={
            "xi-api-key": api_key,
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    return read_response(request)


def read_response(request: Request) -> bytes:
    try:
        with urlopen(request, timeout=120) as response:
            return response.read()
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise ElevenLabsError(f"ElevenLabs HTTP {error.code}: {body}") from error
    except URLError as error:
        raise ElevenLabsError(f"Network error: {error.reason}") from error


def list_voices(api_key: str) -> list[dict[str, Any]]:
    voices: list[dict[str, Any]] = []
    next_page_token: str | None = None

    while True:
        params: dict[str, Any] = {"page_size": 100}
        if next_page_token:
            params["next_page_token"] = next_page_token

        data = request_json(api_key, "/v2/voices", params)
        voices.extend(data.get("voices", []))

        if not data.get("has_more"):
            break
        next_page_token = data.get("next_page_token")
        if not next_page_token:
            break

    return voices


def resolve_voice_id(api_key: str, voice_id: str | None, voice_name: str | None) -> str:
    if voice_id:
        return voice_id
    if not voice_name:
        raise ElevenLabsError("Provide either --voice-id or --voice-name.")

    voices = list_voices(api_key)
    matches = [voice for voice in voices if voice_name_matches(voice.get("name", ""), voice_name)]
    if not matches:
        raise ElevenLabsError(f"No ElevenLabs voice named {voice_name!r} was found.")
    if len(matches) > 1:
        details = ", ".join(
            f"{voice.get('name', '<unnamed>')} ({voice.get('voice_id', '<missing>')})"
            for voice in matches
        )
        raise ElevenLabsError(f"Multiple voices matched {voice_name!r}: {details}")
    return str(matches[0]["voice_id"])


def voice_name_matches(actual_name: str, requested_name: str) -> bool:
    actual = actual_name.casefold().strip()
    requested = requested_name.casefold().strip()
    short_name = re.split(r"\s*[-—]\s*", actual, maxsplit=1)[0].strip()
    return requested in {actual, short_name}


def selected_inputs(args: argparse.Namespace) -> list[Path]:
    files = args.inputs or sorted(args.input_dir.glob("*.txt"))
    ids = parse_id_filter(args)

    selected = []
    for file_path in files:
        story_id = transcript_id(file_path)
        if ids is not None and story_id not in ids:
            continue
        selected.append(file_path)

    if not selected:
        raise ElevenLabsError("No transcript files matched the requested selection.")
    return selected


def parse_id_filter(args: argparse.Namespace) -> set[str] | None:
    explicit_ids = parse_ids(args.ids)
    if args.from_id or args.to_id:
        start = int(args.from_id or "000")
        end = int(args.to_id or "999")
        range_ids = {f"{value:03d}" for value in range(start, end + 1)}
        return explicit_ids & range_ids if explicit_ids else range_ids
    return explicit_ids


def parse_ids(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {item.strip().zfill(3) for item in value.split(",") if item.strip()}


def transcript_id(file_path: Path) -> str:
    match = TRANSCRIPT_ID_RE.match(file_path.name)
    if not match:
        raise ElevenLabsError(f"Transcript filename must start with 3 digits: {file_path}")
    return match.group(1)


def output_path_for(input_path: Path, output_dir: Path) -> Path:
    return output_dir / f"{input_path.stem}.mp3"


def read_text(
    input_path: Path,
    strip_stage_directions: bool,
    keep_english_hesitations: bool = False,
    keep_romanized_brand: bool = False,
) -> str:
    text = input_path.read_text(encoding="utf-8").strip()
    if strip_stage_directions:
        text = STAGE_DIRECTION_RE.sub("", text)
    if not keep_english_hesitations:
        text = ENGLISH_HESITATION_RE.sub("嗯...", text)
    if not keep_romanized_brand:
        text = ROMANIZED_BRAND_RE.sub("露米", text)
    return text


def validate_tts_text(input_path: Path, text: str, allow_latin: bool) -> None:
    if allow_latin:
        return

    check_text = text
    for term in ALLOWED_LATIN_TERMS:
        check_text = re.sub(rf"\b{re.escape(term)}\b", "", check_text, flags=re.IGNORECASE)

    match = LATIN_LETTER_RE.search(check_text)
    if not match:
        return

    start = max(0, match.start() - 20)
    end = min(len(text), match.end() + 20)
    context = text[start:end].replace("\n", "\\n")
    raise ElevenLabsError(
        f"Latin letters remain after cleanup for {input_path}: {context!r}. "
        "Use --preview-text to inspect or --allow-latin to override."
    )


def build_payload(args: argparse.Namespace, text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "text": text,
        "model_id": args.model,
        "voice_settings": {
            "stability": args.stability,
            "similarity_boost": args.similarity_boost,
            "style": args.style,
            "speed": args.speed,
            "use_speaker_boost": not args.no_speaker_boost,
        },
    }
    if args.language_code:
        payload["language_code"] = args.language_code
    return payload


def generate_file(
    api_key: str,
    voice_id: str,
    input_path: Path,
    output_path: Path,
    args: argparse.Namespace,
) -> None:
    text = read_text(
        input_path,
        args.strip_stage_directions,
        args.keep_english_hesitations,
        args.keep_romanized_brand,
    )
    validate_tts_text(input_path, text, args.allow_latin)
    payload = build_payload(args, text)

    if args.dry_run:
        print(f"DRY RUN {input_path} -> {output_path} ({len(text)} chars)")
        if args.preview_text:
            print("----- text sent to ElevenLabs -----")
            print(text)
            print("----- end text -----")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    audio = post_audio(api_key, voice_id, payload, args.output_format)
    output_path.write_bytes(audio)
    elapsed = time.time() - started
    print(f"Wrote {output_path} ({len(audio):,} bytes, {elapsed:.1f}s)")


def main() -> int:
    args = parse_args()

    try:
        needs_api = args.list_voices or not args.dry_run
        api_key = get_api_key(args.api_key_env) if needs_api else ""

        if args.list_voices:
            for voice in sorted(list_voices(api_key), key=lambda item: item.get("name", "")):
                print(f"{voice.get('name', '<unnamed>')}\t{voice.get('voice_id', '<missing>')}")
            return 0

        voice_id = (
            resolve_voice_id(api_key, args.voice_id, args.voice_name)
            if api_key
            else args.voice_id or "<dry-run-voice>"
        )
        inputs = selected_inputs(args)

        for input_path in inputs:
            output_path = output_path_for(input_path, args.output_dir)
            if output_path.exists() and not args.overwrite:
                print(f"Skip existing {output_path}")
                continue
            generate_file(api_key, voice_id, input_path, output_path, args)

        return 0
    except ElevenLabsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
