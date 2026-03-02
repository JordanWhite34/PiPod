#!/usr/bin/env bash
set -euo pipefail

# Configure a Raspberry Pi (Zero 2 W compatible) for an external I2S DAC.
# Default overlay works for common PCM5102A-style boards.
OVERLAY="${1:-hifiberry-dac}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo on the Raspberry Pi:"
  echo "  sudo bash scripts/setup_i2s_dac.sh [dtoverlay-name]"
  exit 1
fi

BOOT_CONFIG=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
  if [[ -f "${candidate}" ]]; then
    BOOT_CONFIG="${candidate}"
    break
  fi
done

if [[ -z "${BOOT_CONFIG}" ]]; then
  echo "Could not find boot config (expected /boot/firmware/config.txt or /boot/config.txt)."
  exit 1
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
cp "${BOOT_CONFIG}" "${BOOT_CONFIG}.pipod-backup-${TIMESTAMP}"

strip_managed_block() {
  local src="$1"
  local tmp="$2"

  awk '
    BEGIN { in_block=0 }
    $0 == "# >>> PiPod I2S DAC >>>" { in_block=1; next }
    $0 == "# <<< PiPod I2S DAC <<<" { in_block=0; next }
    in_block == 0 { print }
  ' "${src}" > "${tmp}"
}

rewrite_with_block() {
  local file="$1"
  local block="$2"

  local tmp
  tmp="$(mktemp)"
  strip_managed_block "${file}" "${tmp}"

  {
    cat "${tmp}"
    echo
    cat "${block}"
  } > "${file}"

  rm -f "${tmp}"
}

BOOT_BLOCK="$(mktemp)"
cat > "${BOOT_BLOCK}" <<BOOT_EOF
# >>> PiPod I2S DAC >>>
dtparam=audio=off
dtoverlay=${OVERLAY}
# <<< PiPod I2S DAC <<<
BOOT_EOF

rewrite_with_block "${BOOT_CONFIG}" "${BOOT_BLOCK}"
rm -f "${BOOT_BLOCK}"

ASOUND_PATH="/etc/asound.conf"
if [[ -f "${ASOUND_PATH}" ]]; then
  cp "${ASOUND_PATH}" "${ASOUND_PATH}.pipod-backup-${TIMESTAMP}"
else
  : > "${ASOUND_PATH}"
fi

ASOUND_BLOCK="$(mktemp)"
cat > "${ASOUND_BLOCK}" <<'ASOUND_EOF'
# >>> PiPod I2S DAC >>>
pcm.!default {
  type plug
  slave.pcm "hw:0,0"
}

ctl.!default {
  type hw
  card 0
}
# <<< PiPod I2S DAC <<<
ASOUND_EOF

rewrite_with_block "${ASOUND_PATH}" "${ASOUND_BLOCK}"
rm -f "${ASOUND_BLOCK}"

echo
echo "I2S DAC configuration written."
echo "  Boot config : ${BOOT_CONFIG}"
echo "  ALSA config : ${ASOUND_PATH}"
echo "  Overlay     : ${OVERLAY}"
echo
echo "Next steps on the Pi:"
echo "  1) Reboot: sudo reboot"
echo "  2) Verify DAC appears: aplay -l"
echo "  3) Test sound: speaker-test -c2 -twav -D default"
echo "  4) Launch PiPod: python3 ai-src/app.py"
