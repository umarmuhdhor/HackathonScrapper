#!/usr/bin/env bash
# Register run_daily.sh with launchd so it runs every morning.
#
# launchd rather than cron: on a laptop that sleeps overnight, cron silently
# skips a missed job, while launchd runs it at the next wake.
set -euo pipefail
cd "$(dirname "$0")"

HOUR="${1:-8}"
MINUTE="${2:-0}"
LABEL="com.hackathon-screening.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROJECT="$(pwd)"

mkdir -p "$HOME/Library/LaunchAgents" data/digests

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PROJECT/run_daily.sh</string>
    </array>
    <key>WorkingDirectory</key><string>$PROJECT</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>$HOUR</integer>
        <key>Minute</key><integer>$MINUTE</integer>
    </dict>
    <key>StandardOutPath</key><string>$PROJECT/data/digests/launchd.out.log</string>
    <key>StandardErrorPath</key><string>$PROJECT/data/digests/launchd.err.log</string>
</dict>
</plist>
PLISTEOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

printf 'Terpasang: %s\n' "$LABEL"
printf 'Jadwal   : setiap hari %02d:%02d\n' "$HOUR" "$MINUTE"
printf 'Plist    : %s\n\n' "$PLIST"
echo "Cek     : launchctl list | grep hackathon-screening"
echo "Uji     : launchctl start $LABEL   (jalankan sekarang juga)"
echo "Hapus   : launchctl unload $PLIST && rm $PLIST"
