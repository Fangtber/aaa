#!/bin/bash
# Watch for SSID "jr"; switch when it appears, roll back on failure.
LOG=/home/fangtb02/wifi_watch_jr.log
TARGET="jr"
FALLBACK="XJKJCXY"

echo "$(date '+%F %T') watcher started (pid $$)" >> "$LOG"

for i in $(seq 1 720); do   # up to 2 hours, 10s interval
  if nmcli -t -f SSID dev wifi list 2>/dev/null | grep -qx "$TARGET"; then
    echo "$(date '+%F %T') '$TARGET' spotted, switching..." >> "$LOG"
    echo '123456' | sudo -S nmcli connection up "$TARGET" >> "$LOG" 2>&1
    sleep 8
    IP=$(ip -4 -o addr show wlan0 | awk '{print $4}' | head -1)
    if [ -n "$IP" ]; then
      echo "$(date '+%F %T') SUCCESS new IP on wlan0: $IP" >> "$LOG"
      hostname -I >> "$LOG" 2>&1
    else
      echo "$(date '+%F %T') switch failed, rolling back to $FALLBACK" >> "$LOG"
      echo '123456' | sudo -S nmcli connection up "$FALLBACK" >> "$LOG" 2>&1
    fi
    exit 0
  fi
  sleep 10
done
echo "$(date '+%F %T') TIMEOUT: '$TARGET' never appeared within 2h" >> "$LOG"
