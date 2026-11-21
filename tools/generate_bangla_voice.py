"""Pre-generate Bangla voice instruction audio files for Fexor Kiosk using TTS.

Run with uv:
    uv run --with edge-tts python tools/generate_bangla_voice.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

VOICE = "bn-BD-NabanitaNeural"

# Mapping of audio file names to their Bangla phrases
PROMPTS = {
    # Liveness Challenge Actions
    "blink": "চোখের পলক ফেলুন",
    "smile": "একটু হাসুন",
    "turn_left": "ধীরে ধীরে মাথা বাঁ দিকে ঘোরান",
    "turn_right": "ধীরে ধীরে মাথা ডান দিকে ঘোরান",
    "look_up": "উপরের দিকে তাকান",

    # Face Guidance & Quality Hints
    "look_straight": "ক্যামেরার দিকে সোজা তাকান",
    "hold_still": "অনুগ্রহ করে স্থির থাকুন",
    "come_closer": "ক্যামেরার একটু কাছে আসুন",
    "one_person": "ক্যামেরার সামনে শুধু একজন থাকুন",
    "too_dark": "পর্যাপ্ত আলোতে আসুন",

    # QR Scanning
    "scan_qr": "আপনার কিউআর কোডটি ক্যামেরার সামনে ধরুন",

    # Verification Results
    "checked_in": "চেক ইন সম্পন্ন হয়েছে, ধন্যবাদ",
    "checked_out": "চেক আউট সম্পন্ন হয়েছে, ধন্যবাদ",
    "marked_present": "উপস্থিতি নিশ্চিত হয়েছে, ধন্যবাদ",
    "already_recorded": "আপনার উপস্থিতি ইতিমধ্যে রেকর্ড করা হয়েছে",
    "failed": "যাচাইকরণ সম্পন্ন হয়নি, অনুগ্রহ করে আবার চেষ্টা করুন",
}


async def generate_prompt(name: str, text: str, output_dir: Path) -> None:
    import edge_tts

    out_file = output_dir / f"{name}.mp3"
    print(f"Generating {name}.mp3 ('{text}')...")
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(str(out_file))
    print(f"  -> Saved {out_file.name} ({out_file.stat().st_size} bytes)")


async def _generate_all(output_dir: Path) -> None:
    print(f"Generating Bangla voice instructions with voice {VOICE}...")
    for name, text in PROMPTS.items():
        await generate_prompt(name, text, output_dir)
    print(f"\nSuccessfully generated {len(PROMPTS)} Bangla voice audio files in {output_dir}")


def main() -> None:
    output_dir = Path(__file__).resolve().parent.parent / "kiosk" / "src" / "fexor_kiosk" / "static" / "audio"
    output_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(_generate_all(output_dir))


if __name__ == "__main__":
    main()
