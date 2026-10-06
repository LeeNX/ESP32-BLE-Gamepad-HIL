# Bluepad32 observer (see platformio.ini). Bluepad32 stays outside this repo, like the ESP32-BLE-Gamepad library is for
# hil_runner: HIL_BLUEPAD32_DIR names the checkout.
cmake_minimum_required(VERSION 3.13)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

if(NOT DEFINED ENV{HIL_BLUEPAD32_DIR})
    message(FATAL_ERROR "HIL_BLUEPAD32_DIR is not set: point it at a Bluepad32 checkout (builder/build-observers.sh does)")
endif()
get_filename_component(BLUEPAD32_ROOT "$ENV{HIL_BLUEPAD32_DIR}" ABSOLUTE)
set(ENV{BTSTACK_ROOT} ${BLUEPAD32_ROOT}/external/btstack)

set(EXTRA_COMPONENT_DIRS ${BLUEPAD32_ROOT}/src/components)

# Firmware version, shown by "version?". build-observers.sh sets HIL_FW_VERSION to bp32obs-<bluepad32 sha8>.
if(DEFINED ENV{HIL_FW_VERSION})
    set(PROJECT_VER "$ENV{HIL_FW_VERSION}")
else()
    set(PROJECT_VER "bp32obs-dev")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
include($ENV{BTSTACK_ROOT}/port/esp32/components/btstack/cmake/BTstack_project.cmake)

project(bluepad32_hil_observer)
