# HIL benchmark

> Debian GNU/Linux 13 (trixie) · kernel 6.18.50+rpt-rpi-v8 · aarch64 · BlueZ 5.82 · Python 3.13.5, load 0.2

| Board | Profile | Report B | Descr B | Conn ms | MTU | links | btn e2e p50/p99 ms | axis p50 ms | clean Hz | dropped |
|---|---|---|---|---|---|---|---|---|---|---|
| esp32c3 | minimal | 5 | 52 | 48.75 | 255 | 3 | 18.617/67.49 | 18.617 | 62.6 | 0 |
| esp32c3 | specials | 7 | 118 | 48.75 | 255 | 3 | 18.606/67.474 | 18.602 | 62.7 | 0 |
| esp32c3 | maxbtn | 16 | 25 | 48.75 | 255 | 3 | 18.601/67.425 | - | - | 0 |
| esp32c3 | default | 28 | 102 | 48.75 | 255 | 3 | 18.603/67.419 | 18.593 | 62.7 | 0 |
| esp32dev | minimal | 5 | 52 | 48.75 | 255 | 3 | 18.603/67.461 | 18.61 | 87.9 | 0 |
| esp32dev | specials | 7 | 118 | 48.75 | 255 | 3 | 18.609/67.455 | 18.609 | 128.8 | 0 |
| esp32dev | maxbtn | 16 | 25 | 48.75 | 255 | 3 | 18.608/68.08 | - | - | 0 |
| esp32dev | default | 28 | 102 | 48.75 | 255 | 3 | 18.611/67.489 | 18.603 | 101.6 | 0 |
| esp32s3 | minimal | 5 | 52 | 48.75 | 255 | 3 | 18.615/22.253 | 18.602 | 128.8 | 0 |
| esp32s3 | specials | 7 | 118 | 48.75 | 255 | 3 | 18.588/65.433 | 18.593 | 131.2 | 0 |
| esp32s3 | maxbtn | 16 | 25 | 48.75 | 255 | 3 | 18.601/18.879 | - | - | 0 |
| esp32s3 | default | 28 | 102 | 48.75 | 255 | 3 | 18.61/67.168 | 18.595 | 131.1 | 0 |

_links = BLE connections live on the adapter during the sweep (`n*` = other gamepads were being driven too — a contention run)._
