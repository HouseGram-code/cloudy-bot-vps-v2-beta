#!/usr/bin/env python3
"""Simple supplementary guard, not a replacement for GitHub secret scanning."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = [
    re.compile(r"[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{25,110}"),
    re.compile(r"(?:DISCORD_TOKEN|FREESTYLE_API_KEY)\s*[:=]\s*['\"]?([A-Za-z0-9_.-]{35,})"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]

def main():
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, text=True)
    # In an extracted ZIP there is no .git: scan all distributable text files.
    files = [ROOT / name for name in result.stdout.split('\0') if name] if result.returncode == 0 else list(ROOT.rglob('*'))
    found = []
    for file in files:
        if not file.is_file() or any(p in {'.git', '.venv', 'node_modules', '__pycache__'} for p in file.parts):
            continue
        if file.name == '.env' or file.name.startswith('.env.') and file.name != '.env.example':
            if result.returncode == 0:
                found.append(str(file.relative_to(ROOT)))
            continue
        try:
            text = file.read_text(encoding='utf-8')
        except (UnicodeError, OSError):
            continue
        if any(pattern.search(text) for pattern in PATTERNS):
            found.append(str(file.relative_to(ROOT)))
    if found:
        print('Possible secrets found in files (values are deliberately hidden):')
        print('\n'.join(sorted(set(found))))
        return 1
    print('PASS: no obvious secrets in distributable/tracked files. Never bypass GitHub push protection.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
