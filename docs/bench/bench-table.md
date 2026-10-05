# HIL benchmark

> Debian GNU/Linux 13 (trixie) · kernel 6.18.50+rpt-rpi-v8 · aarch64 · BlueZ 5.82 · Python 3.13.5, load 0.1

| Board | Profile | Report B | Descr B | Conn ms | MTU | links | btn e2e p50/p99 ms | axis p50 ms | clean Hz | dropped |
|---|---|---|---|---|---|---|---|---|---|---|
| esp32c3 | minimal | 5 | 52 | 8.75 | 255 | 3 | 17.78/19.066 | 17.745 | 62.8 | 0 |
| esp32c3 | specials | 7 | 118 | 8.75 | 255 | 3 | 17.778/19.056 | 17.756 | 63.1 | 0 |
| esp32c3 | maxbtn | 16 | 25 | 8.75 | 255 | 3 | 17.766/20.636 | - | - | 0 |
| esp32c3 | maxfeat | 20 | 123 | 8.75 | 255 | 3 | 17.772/19.14 | 17.747 | 62.7 | 0 |
| esp32c3 | default | 28 | 102 | 8.75 | 255 | 3 | 17.771/19.768 | 17.756 | 63.0 | 0 |
| esp32dev | minimal | 5 | 52 | 43.75 | 255 | 2 | 13.633/57.483 | 13.618 | 131.2 | 0 |
| esp32dev | specials | 7 | 118 | 43.75 | 255 | 2 | 13.641/57.511 | 13.634 | 99.7 | 0 |
| esp32dev | maxbtn | 16 | 25 | 43.75 | 255 | 2 | 13.639/57.489 | - | - | 0 |
| esp32dev | maxfeat | 20 | 123 | 43.75 | 255 | 2 | 13.639/57.521 | 13.608 | 98.8 | 0 |
| esp32dev | default | 28 | 102 | 43.75 | 255 | 2 | 13.629/57.538 | 13.638 | 130.7 | 0 |
| esp32s3 | minimal | 5 | 52 | 8.75 | 255 | 1 | 4.914/12.578 | 4.888 | 258.2 | 0 |
| esp32s3 | specials | 7 | 118 | 8.75 | 255 | 1 | 4.954/12.526 | 4.863 | 260.4 | 0 |
| esp32s3 | maxbtn | 16 | 25 | 8.75 | 255 | 1 | 5.041/12.513 | - | - | 0 |
| esp32s3 | maxfeat | 20 | 123 | 8.75 | 255 | 1 | 4.907/12.652 | 4.869 | 276.4 | 0 |
| esp32s3 | default | 28 | 102 | 8.75 | 255 | 1 | 4.975/13.35 | 4.883 | 273.7 | 0 |

_links = BLE connections live on the adapter during the sweep (`n*` = other gamepads were being driven too — a contention run)._
