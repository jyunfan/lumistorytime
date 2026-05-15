#!/usr/bin/env python3
"""Mix narration MP3 files with intro and outro music using ffmpeg.

Examples:
  python3 scripts/mix_release.py
  python3 scripts/mix_release.py --from-id 003 --to-id 010
  python3 scripts/mix_release.py --music music.mp3 --voice-dir audio/voice --release-dir audio/release

For each audio/voice/*.mp3 file, this creates audio/release/*.mp3 with:
  intro music -> narration -> outro music
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


ID_RE = re.compile(r"^(\d{3})-")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mix voice MP3 files with intro and outro music."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        help="Voice MP3 files. If omitted, files are selected from --voice-dir.",
    )
    parser.add_argument(
        "--voice-dir",
        type=Path,
        default=Path("audio/voice"),
        help="Directory containing voice MP3 files.",
    )
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=Path("audio/release"),
        help="Directory for mixed release MP3 files.",
    )
    parser.add_argument(
        "--music",
        type=Path,
        default=Path("music/Color_Chef_Kids.mp3"),
        help="Music MP3 file used for both intro and outro.",
    )
    parser.add_argument("--ids", help="Comma-separated ids, for example: 003,004,010.")
    parser.add_argument("--from-id", help="First id to include, e.g. 004.")
    parser.add_argument("--to-id", help="Last id to include, e.g. 010.")
    parser.add_argument(
        "--intro-seconds",
        type=float,
        default=10.0,
        help="Intro music duration before narration starts.",
    )
    parser.add_argument(
        "--outro-seconds",
        type=float,
        default=10.0,
        help="Outro music duration after narration ends.",
    )
    parser.add_argument(
        "--fade-in-seconds",
        type=float,
        default=2.0,
        help="Music fade-in duration.",
    )
    parser.add_argument(
        "--fade-out-seconds",
        type=float,
        default=3.0,
        help="Music fade-out duration.",
    )
    parser.add_argument(
        "--music-volume",
        type=float,
        default=0.5,
        help="Music volume multiplier.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate files even if the release MP3 already exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned ffmpeg work without running it.",
    )
    return parser.parse_args()


def parse_ids(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {item.strip().zfill(3) for item in value.split(",") if item.strip()}


def parse_id_filter(args: argparse.Namespace) -> set[str] | None:
    explicit_ids = parse_ids(args.ids)
    if args.from_id or args.to_id:
        start = int(args.from_id or "000")
        end = int(args.to_id or "999")
        range_ids = {f"{value:03d}" for value in range(start, end + 1)}
        return explicit_ids & range_ids if explicit_ids else range_ids
    return explicit_ids


def file_id(path: Path) -> str:
    match = ID_RE.match(path.name)
    if not match:
        raise ValueError(f"Voice filename must start with 3 digits: {path}")
    return match.group(1)


def selected_inputs(args: argparse.Namespace) -> list[Path]:
    files = args.inputs or sorted(args.voice_dir.glob("*.mp3"))
    ids = parse_id_filter(args)
    selected = []
    for path in files:
        story_id = file_id(path)
        if ids is not None and story_id not in ids:
            continue
        selected.append(path)
    if not selected:
        raise ValueError("No voice MP3 files matched the requested selection.")
    return selected


def validate_args(args: argparse.Namespace) -> None:
    if not shutil.which("ffmpeg"):
        raise ValueError("ffmpeg was not found in PATH.")
    if not args.music.exists():
        raise ValueError(f"Music file not found: {args.music}")
    for name in ("intro_seconds", "outro_seconds", "fade_in_seconds", "fade_out_seconds"):
        if getattr(args, name) < 0:
            raise ValueError(f"--{name.replace('_', '-')} must be >= 0.")
    if args.fade_out_seconds > args.intro_seconds or args.fade_out_seconds > args.outro_seconds:
        raise ValueError("--fade-out-seconds must be no longer than intro/outro duration.")


def output_path_for(input_path: Path, release_dir: Path) -> Path:
    return release_dir / input_path.name


def build_filter(args: argparse.Namespace) -> str:
    intro = args.intro_seconds
    outro = args.outro_seconds
    fade_in = args.fade_in_seconds
    fade_out = args.fade_out_seconds
    volume = args.music_volume
    delay_ms = round(intro * 1000)
    intro_fade_out_start = max(0.0, intro - fade_out)
    outro_fade_out_start = max(0.0, outro - fade_out)

    return (
        f"[1:a]atrim=0:{intro},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={fade_in},"
        f"afade=t=out:st={intro_fade_out_start}:d={fade_out},"
        f"volume={volume}[intro];"
        f"[1:a]atrim=0:{outro},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={fade_in},"
        f"afade=t=out:st={outro_fade_out_start}:d={fade_out},"
        f"volume={volume}[outro];"
        f"[0:a]adelay={delay_ms}:all=1[voice];"
        f"[intro][voice]amix=inputs=2:duration=longest:dropout_transition=0[body];"
        f"[body][outro]concat=n=2:v=0:a=1[aout]"
    )


def build_command(args: argparse.Namespace, input_path: Path, output_path: Path) -> list[str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y" if args.overwrite else "-n",
        "-i",
        str(input_path),
        "-i",
        str(args.music),
        "-filter_complex",
        build_filter(args),
        "-map",
        "[aout]",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(output_path),
    ]
    return command


def mix_one(args: argparse.Namespace, input_path: Path) -> None:
    output_path = output_path_for(input_path, args.release_dir)

    if output_path.exists() and not args.overwrite:
        print(f"Skip existing {output_path}")
        return

    command = build_command(args, input_path, output_path)
    if args.dry_run:
        print("DRY RUN " + " ".join(command))
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Mix {input_path} -> {output_path}", flush=True)
    subprocess.run(command, check=True)


def main() -> int:
    args = parse_args()
    try:
        validate_args(args)
        for input_path in selected_inputs(args):
            mix_one(args, input_path)
        return 0
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
