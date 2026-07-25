"""Launcher - activate venv and run app.py

5.7£ºÈ¥³ýÈÎºÎ¸öÈËµçÄÔ¾ø¶ÔÂ·¾¶Ó²±àÂë¡£³ÌÐòÄ¿Â¼ÓÉ±¾ÎÄ¼þÎ»ÖÃÍÆµ¼£¨²Ö¿â¿ÉÕûÌåÒÆ¶¯£©£¬
python ½âÊÍÆ÷ÓÅÏÈ¼¶£º»·¾³±äÁ¿ AUDIOBOOK_STUDIO_PYTHON > ²Ö¿âÍ¬¼¶ index-tts/.venv¡£
"""
import os
import shutil
import subprocess

# ³ÌÐòÄ¿Â¼£º±¾ÎÄ¼þËùÔÚÄ¿Â¼£¨²Ö¿â¿ÉÕûÌåÒÆ¶¯£¬²»ÒÀÀµ¾ø¶ÔÂ·¾¶£©
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# python ½âÊÍÆ÷£º»·¾³±äÁ¿ÓÅÏÈ£¬·ñÔòÈ¡²Ö¿âÍ¬¼¶µÄ index-tts venv£¨Ïà¶ÔÂ·¾¶£¬¿ÉÒÆÖ²£©¡£
# ²»ÔÙÓ²±àÂë¸öÈËµçÄÔ¾ø¶ÔÂ·¾¶£¬ÇÐ»»»úÆ÷ / ÒÆ¶¯²Ö¿âºóÎÞÐèÐÞ¸Ä±¾ÎÄ¼þ¡£
PYTHON = os.environ.get("AUDIOBOOK_STUDIO_PYTHON") or os.path.join(
    BASE_DIR, "..", "index-tts", ".venv", "Scripts", "python.exe"
)


def main() -> None:
    """Entry point: prepare environment, run dependency check and start app."""
    os.chdir(BASE_DIR)

    # Ë«»÷ºóµÄÊ×¸öÖÐÎÄ¼´Ê±·´À¡£¨ÓÉ Python Êä³ö£¬±ÜÃâ .bat ÖÐÎÄ±àÂëÂÒÂë£©
    print("ÓÐÉùÊé¹¤×÷Ì¨Æô¶¯ÖÐ£¬ÇëÉÔºó...")

    # ¼ì²éÔËÐÐ»·¾³£¨ÒÀÀµ¼ì²é½ÏÂý£¬ÏÈ¸ø³öÌáÊ¾£¬±ÜÃâ¿ØÖÆÌ¨¿ÕÆÁ£©
    print("ÕýÔÚ¼ì²éÔËÐÐ»·¾³£¬ÇëÉÔºò...")

    # Check dependency
    result = subprocess.run(
        [PYTHON, "-c", "import gradio"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        subprocess.run([PYTHON, "-m", "pip", "install", "gradio", "pydub"], check=True)

    # Check scientific / audio deps needed by the export post-processing chain
    # (numpy, scipy, pyloudnorm for LUFS-16; mutagen for ID3 / chapter tags).
    result = subprocess.run(
        [PYTHON, "-c", "import numpy, scipy, pyloudnorm, mutagen"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        subprocess.run(
            [PYTHON, "-m", "pip", "install", "numpy", "scipy", "pyloudnorm", "mutagen"],
            check=True,
        )

    # ffmpeg is a system binary (NOT a pip package). Exporting mp3/m4b needs it;
    # if it is missing we must warn loudly instead of silently degrading.
    # 5.8£ºÈ±Ê§Ê±ÏÔÊ½±¨´í£¨µ¼³ö mp3/m4b »áÅ× ExportError£¬ÒÑÉú³ÉµÄÖÐ¼ä WAV ÈÔ±£Áô£©£¬
    # ²»ÔÙ¡°¾²Ä¬»ØÍË WAV¡±¡£
    if shutil.which("ffmpeg") is None:
        print()
        print("=" * 50)
        print("  ⚠ ¾¯¸æ£ºÎ´¼ì²âµ½ ffmpeg£¡")
        print("  µ¼³ö mp3 / m4b ÐèÒª ffmpeg£¨ÏµÍ³¶þ½øÖÆ£¬²»Í¨¹ý pip °²×°£©¡£")
        print("  È±Ê§Ê±µ¼³ö»áÏÔÊ½±¨´í£¨ÒÑÉú³ÉµÄÖÐ¼ä WAV ÈÔ±£Áô£©£¬")
        print("  ÇëÏÂÔØ ffmpeg ²¢¼ÓÈë PATH£¬»ò¸ÄÓÃ WAV ¸ñÊ½µ¼³ö¡£")
        print("  ÏÂÔØµØÖ·£ºhttps://ffmpeg.org/download.html")
        print("=" * 50)
        print()

    # Start app
    print()
    print("=" * 50)
    print("       ÓÐÉùÊéºÏ³É¹¤×÷Ì¨ | Audiobook Studio v3.1.0")
    print("=" * 50)
    print()
    print("  ä¯ÀÀÆ÷·ÃÎÊµØÖ·:")
    print("  -->  http://localhost:7862  <--")
    print()
    print("  Ê×´Î¼ÓÔØÄ£ÐÍÐèÒªµÈ´ý 10-30 Ãë")
    print("  ¹Ø±Õ´Ë´°¿Ú¼´¿ÉÍ£Ö¹·þÎñ")
    print()
    print("=" * 50)
    print()

    # ¼ÓÔØÓïÒôºÏ³ÉÒýÇæ£¨Ê×´ÎÔ¼ 10-30 Ãë£©£¬ÏÈ¸ø³öÌáÊ¾
    print("ÕýÔÚ¼ÓÔØÓïÒôºÏ³ÉÒýÇæ£¬Ê×´ÎÔ¼ 10-30 Ãë...")
    subprocess.run([PYTHON, "app.py"], check=True)


if __name__ == "__main__":
    main()
