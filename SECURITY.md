# Security

SwiftCut is a personal project, updated occasionally. It runs locally, makes no network
connections, and opens video files with the bundled FFmpeg and Qt.

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Use GitHub's private reporting
instead: on this repository, go to **Security → Report a vulnerability**. Include the SwiftCut
version, what you found, and how to reproduce it (a small sample file helps, if relevant).

Most vulnerabilities in video tools come from the decoding libraries. If an issue is in FFmpeg
or Qt itself, it will normally be fixed by updating the bundled version in a new SwiftCut release.

Only the latest release receives fixes.
