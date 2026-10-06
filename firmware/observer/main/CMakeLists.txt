idf_component_register(SRCS "main.c" "observer.c"
                    INCLUDE_DIRS "."
                    REQUIRES "bluepad32" "btstack")

# HIL_ALLOW_ADDR=AA:BB:CC:DD:EE:FF bakes in a pairing filter (see observer.c); `allow <addr>` sets it at runtime.
if(DEFINED ENV{HIL_ALLOW_ADDR})
    target_compile_definitions(${COMPONENT_LIB} PRIVATE HIL_ALLOW_ADDR="$ENV{HIL_ALLOW_ADDR}")
endif()
