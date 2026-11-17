idf_component_register(SRCS "main.c" "observer.c" "hil_led.c"
                    INCLUDE_DIRS "." "../../include"
                    REQUIRES "bluepad32" "btstack" "app_update" "esp_partition" "esp_driver_gpio" "esp_driver_rmt")

# HIL_ALLOW_ADDR=AA:BB:CC:DD:EE:FF bakes in a pairing filter (see observer.c); `allow <addr>` sets it at runtime.
if(DEFINED ENV{HIL_ALLOW_ADDR})
    target_compile_definitions(${COMPONENT_LIB} PRIVATE HIL_ALLOW_ADDR="$ENV{HIL_ALLOW_ADDR}")
endif()
# Status LEDs (hil_led.h): builder/build-observers.sh sets these from the board's led_pin / conn_led_pin
# (builder/led-pin.sh) -- internal plumbing, not settings of their own.
if(DEFINED ENV{HIL_OBS_LED_PIN} AND NOT "$ENV{HIL_OBS_LED_PIN}" STREQUAL "")
    target_compile_definitions(${COMPONENT_LIB} PRIVATE HIL_LED_PIN=$ENV{HIL_OBS_LED_PIN})
endif()
if("$ENV{HIL_OBS_LED_RGB}" STREQUAL "1")
    target_compile_definitions(${COMPONENT_LIB} PRIVATE HIL_LED_RGB)
endif()
if(DEFINED ENV{HIL_OBS_CONN_LED_PIN} AND NOT "$ENV{HIL_OBS_CONN_LED_PIN}" STREQUAL "")
    target_compile_definitions(${COMPONENT_LIB} PRIVATE HIL_CONN_LED_PIN=$ENV{HIL_OBS_CONN_LED_PIN})
endif()
