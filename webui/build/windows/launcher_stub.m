#include <stdbool.h>

// Fallback only: ios_cross_compile.py links this when the stage has no ObjC launcher
// (webui/overlays/dm/ios/Sources/DmLauncher.m), because the Windows cross-build cannot compile
// upstream's Swift launcher. Returning false starts the game at once with its defaults.
bool dm_show_launcher(void *view_controller, void (*done)(void)) {
    (void)view_controller;
    (void)done;
    return false;
}
