# shellcheck shell=bash
# Status LED settings, per board, shared by builder/build.sh (hil_runner) and builder/build-observers.sh (Bluepad32
# observer), so a board's LED behaves the same whichever firmware it runs. Sourced; the caller defines cfg().
# docs/rig-hardware.md has the details.
#
#   activity LED:    $HIL_LED_PIN_<BOARD>      or [board.<board>].led_pin       <gpio> | RGB<gpio> (RGB48, rgb:48)
#   connection LED:  $HIL_CONN_LED_PIN_<BOARD> or [board.<board>].conn_led_pin  <gpio>
#
# <BOARD> is the [board.<name>] key, uppercased (HIL_LED_PIN_ESP32S3). The env var wins over the config, so a CI
# runner can set it without a config edit. `RGB<gpio>` is a WS2812-style addressable LED (the S3 DevKitC-1's onboard
# one is RGB48, or RGB38 on a v1.1 board): same setting, no extra variable.

board_led_setting() {  # <board> <config key> <env prefix, e.g. LED_PIN>
  local env_var v
  env_var="HIL_${3}_$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"
  v="${!env_var:-}"
  [[ -n "$v" ]] && { echo "$v"; return; }
  cfg "board.$1.$2"
}

# GPIOs are emitted in decimal: "RGB048" -> 48, never -DHIL_LED_PIN=048 (an octal literal in C, invalid here; and
# "010" would silently be GPIO8).
# board_leds <board>: sets LED_GPIO (activity LED GPIO, "" if none), LED_RGB (1 for an RGB LED, else 0) and
# CONN_LED_GPIO ("" if none), and prints what it found. Returns 2, with a message, on a value it can't parse.
# shellcheck disable=SC2034  # LED_GPIO / LED_RGB / CONN_LED_GPIO are read by the sourcing script
board_leds() {
  local board=$1 v c
  LED_GPIO=""; LED_RGB=0; CONN_LED_GPIO=""
  v=$(board_led_setting "$board" led_pin LED_PIN)
  if [[ "$v" =~ ^[Rr][Gg][Bb]:?([0-9]+)$ ]]; then
    LED_GPIO=$((10#${BASH_REMATCH[1]})); LED_RGB=1
    echo "== $board activity LED: RGB on GPIO $LED_GPIO"
  elif [[ "$v" =~ ^[0-9]+$ ]]; then
    LED_GPIO=$((10#$v))
    echo "== $board activity LED: GPIO $LED_GPIO"
  elif [[ -n "$v" ]]; then
    echo "!! $board: led_pin '$v' is neither <gpio> nor RGB<gpio>" >&2
    return 2
  fi
  c=$(board_led_setting "$board" conn_led_pin CONN_LED_PIN)
  if [[ "$c" =~ ^[0-9]+$ ]]; then
    CONN_LED_GPIO=$((10#$c))
    echo "== $board connection LED: GPIO $CONN_LED_GPIO"
  elif [[ -n "$c" ]]; then
    echo "!! $board: conn_led_pin '$c' is not a <gpio>" >&2
    return 2
  fi
}
