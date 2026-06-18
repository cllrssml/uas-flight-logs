dji-log-parser binaries
=======================

This directory holds the platform-specific dji-log-parser binaries.
The workflow package ships without them (they are git-ignored); you must
place the correct binary here before running ingest_flights.

Download
--------
Go to: https://github.com/lvauvillier/dji-log-parser/releases

Download the binary for your platform and place it in this directory:

  Linux  : dji-log-parser-linux-x86_64
  Windows: dji-log-parser-win-x86_64.exe
  macOS  : dji-log-parser-macos-x86_64

After downloading on Linux/macOS, make the binary executable:
  chmod +x uas_tasks/bin/dji-log-parser-linux-x86_64

Version pinning
---------------
Pin the release version in CLAUDE.md and the workflow README so that
testers know which version was validated.

Fallback path
-------------
If the binary is not present in this directory, uas_tasks/_binary.py
also checks ~/bin/dji-log-parser (Linux/macOS) or ~/bin/dji-log-parser.exe
(Windows) — useful during local development.
