cmake_minimum_required(VERSION 3.22)

#
# This file is generated only once,
# and is not re-generated if converter is called multiple times.
#
# User is free to modify the file as much as necessary
#

# Setup compiler settings
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)


# Define the build type
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Debug")
endif()

# Set the project name
set(CMAKE_PROJECT_NAME driver_fw)

# Enable compile command to ease indexing with e.g. clangd
set(CMAKE_EXPORT_COMPILE_COMMANDS TRUE)

# Core project settings
project(${CMAKE_PROJECT_NAME})
message("Build type: " ${CMAKE_BUILD_TYPE})

# 設定保存用に Flash 末尾 4KB を除外したリンカスクリプトを使う。
# CubeMX が再生成する STM32L433xx_FLASH.ld やツールチェーンファイルを変更しないよう、ここで差し替える。
string(REPLACE "STM32L433xx_FLASH.ld" "App/STM32L433xx_app.ld"
       CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS}")

# Enable CMake support for ASM and C languages
enable_language(C ASM)

# Create an executable object type
add_executable(${CMAKE_PROJECT_NAME})

# Add STM32CubeMX generated sources
add_subdirectory(cmake/stm32cubemx)

# Link directories setup
target_link_directories(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined library search paths
)

# Add sources to executable
set(APP_SOURCES
    App/Src/app.c
    App/Src/app_irq.c
    App/Src/app_main.c
    App/Src/board.c
    App/Src/comm.c
    App/Src/drv.c
    App/Src/monitor.c
    App/Src/position.c
    App/Src/settings.c
    App/Src/stepper.c
    App/Src/symtab.c
    App/Src/ui.c
    App/Src/usb_proto.c
)
target_sources(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user sources here
    ${APP_SOURCES}
)

# アプリコードだけ警告を強める。Debug でもパルス生成割り込みの処理時間を確保するため -Og にする
set_source_files_properties(${APP_SOURCES} PROPERTIES
    COMPILE_OPTIONS "-Wextra;-Wno-unused-parameter;$<$<CONFIG:Debug>:-Og>"
)

# ELF に GNU build-id（SHA1）を入れる（FR-USB-34）。リンカスクリプト App/STM32L433xx_app.ld の
# .note.gnu.build-id に配置され、USB の EXCHANGE_ID が返す
target_link_options(${CMAKE_PROJECT_NAME} PRIVATE
    -Wl,--build-id=sha1
)

# Add include paths
target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined include paths
    App/Inc
)

# Add project symbols (macros)
target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined symbols
)

# 電源投入時の安全状態試験モード（docs/test_poweron_safe_state.md）。製品には書き込まない
option(FW_TEST_POWERON "Power-on safe state test mode" OFF)
set(FW_TEST_POWERON_PREINIT_MS 0 CACHE STRING "Hold time before board_init() in test mode [ms]")
if(FW_TEST_POWERON)
    message(WARNING "FW_TEST_POWERON=ON: test firmware (preinit hold ${FW_TEST_POWERON_PREINIT_MS} ms)")
    target_sources(${CMAKE_PROJECT_NAME} PRIVATE App/Src/test_poweron.c)
    set_source_files_properties(App/Src/test_poweron.c PROPERTIES
        COMPILE_OPTIONS "-Wextra;-Wno-unused-parameter"
    )
    target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE
        FW_TEST_POWERON
        FW_TEST_POWERON_PREINIT_MS=${FW_TEST_POWERON_PREINIT_MS}u
    )
endif()

# Remove wrong libob.a library dependency when using cpp files
list(REMOVE_ITEM CMAKE_C_IMPLICIT_LINK_LIBRARIES ob)

# Add linked libraries
target_link_libraries(${CMAKE_PROJECT_NAME}
    stm32cubemx

    # Add user defined libraries
)
